# -*- coding:utf-8 -*-
__all__ = ['Browser']

import asyncio
import base64
import gzip
import hashlib
import json
import random
import re
import socket
import time

import aiohttp
import cv2 as cv
import numpy as np
from bs4 import BeautifulSoup
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from google.protobuf.json_format import ParseDict

from ._config import CONFIG
from ._logger import get_logger
from ._types import JSON_DECODER, Ats, BasicUserInfo, Comments, Posts, Replys, Searches, Thread, Threads, UserInfo
from .tieba_proto import (
    CommitPersonalMsgReqIdl_pb2,
    CommitPersonalMsgResIdl_pb2,
    CommonReq_pb2,
    FrsPageReqIdl_pb2,
    FrsPageResIdl_pb2,
    GetBawuInfoReqIdl_pb2,
    GetBawuInfoResIdl_pb2,
    GetUserByTiebaUidReqIdl_pb2,
    GetUserByTiebaUidResIdl_pb2,
    GetUserInfoReqIdl_pb2,
    GetUserInfoResIdl_pb2,
    PbFloorReqIdl_pb2,
    PbFloorResIdl_pb2,
    PbPageReqIdl_pb2,
    PbPageResIdl_pb2,
    ReplyMeReqIdl_pb2,
    ReplyMeResIdl_pb2,
    SearchPostForumReqIdl_pb2,
    SearchPostForumResIdl_pb2,
    ThreadInfo_pb2,
    UpdateClientInfoReqIdl_pb2,
    UpdateClientInfoResIdl_pb2,
    User_pb2,
)

LOG = get_logger()


class Sessions(object):
    """
    保持会话

    Args:
        BDUSS_key (str | None): 用于从config.json中提取BDUSS. Defaults to None.
    """

    __slots__ = [
        '_timeout',
        '_connector',
        'app',
        'app_proto',
        'web',
        '_app_websocket',
        'websocket',
        '_ws_password',
        '_ws_aes_chiper',
        'BDUSS',
        'STOKEN',
    ]

    def __init__(self, BDUSS_key: str | None = None) -> None:

        if BDUSS_key:
            self.BDUSS: str = CONFIG['BDUSS'].get(BDUSS_key, '')
            self.STOKEN: str = CONFIG['STOKEN'].get(BDUSS_key, '')
        else:
            self.BDUSS: str = ''
            self.STOKEN: str = ''

        self.app: aiohttp.ClientSession = None
        self.app_proto: aiohttp.ClientSession = None
        self._app_websocket: aiohttp.ClientSession = None
        self.web: aiohttp.ClientSession = None
        self.websocket: aiohttp.ClientWebSocketResponse = None
        self._ws_password: bytes = None
        self._ws_aes_chiper = None

    async def enter(self) -> "Sessions":
        self._timeout = aiohttp.ClientTimeout(connect=5, sock_connect=3, sock_read=10)
        self._connector = aiohttp.TCPConnector(
            ttl_dns_cache=600, keepalive_timeout=90, limit=0, family=socket.AF_INET, ssl=False
        )
        _trust_env = False

        # Init app client
        app_headers = {
            aiohttp.hdrs.USER_AGENT: 'bdtb for Android 12.12.1.0',
            aiohttp.hdrs.CONNECTION: 'keep-alive',
            aiohttp.hdrs.ACCEPT_ENCODING: 'gzip',
            aiohttp.hdrs.HOST: 'c.tieba.baidu.com',
        }
        self.app = aiohttp.ClientSession(
            connector=self._connector,
            headers=app_headers,
            connector_owner=False,
            raise_for_status=True,
            timeout=self._timeout,
            read_bufsize=1 << 18,  # 256KiB
            trust_env=_trust_env,
        )

        # Init app protobuf client
        app_proto_headers = {
            aiohttp.hdrs.USER_AGENT: 'bdtb for Android 12.12.1.0',
            'x_bd_data_type': 'protobuf',
            aiohttp.hdrs.CONNECTION: 'keep-alive',
            aiohttp.hdrs.ACCEPT_ENCODING: 'gzip',
            aiohttp.hdrs.HOST: 'c.tieba.baidu.com',
        }
        self.app_proto = aiohttp.ClientSession(
            connector=self._connector,
            headers=app_proto_headers,
            connector_owner=False,
            raise_for_status=True,
            timeout=self._timeout,
            read_bufsize=1 << 18,  # 256KiB
            trust_env=_trust_env,
        )

        # Init web client
        web_headers = {
            aiohttp.hdrs.USER_AGENT: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:100.0) Gecko/20100101 Firefox/100.0',
            aiohttp.hdrs.ACCEPT_ENCODING: 'gzip, deflate, br',
            aiohttp.hdrs.CACHE_CONTROL: 'no-cache',
            aiohttp.hdrs.CONNECTION: 'keep-alive',
        }
        web_cookie_jar = aiohttp.CookieJar()
        web_cookie_jar.update_cookies({'BDUSS': self.BDUSS, 'STOKEN': self.STOKEN})
        self.web = aiohttp.ClientSession(
            connector=self._connector,
            headers=web_headers,
            cookie_jar=web_cookie_jar,
            connector_owner=False,
            raise_for_status=True,
            timeout=self._timeout,
            read_bufsize=1 << 20,  # 1MiB
            trust_env=_trust_env,
        )

        # Init app websocket client
        app_websocket_headers = {
            aiohttp.hdrs.HOST: 'im.tieba.baidu.com:8000',
            aiohttp.hdrs.SEC_WEBSOCKET_EXTENSIONS: 'im_version=2.3',
        }
        self._app_websocket = aiohttp.ClientSession(
            connector=self._connector,
            headers=app_websocket_headers,
            connector_owner=False,
            raise_for_status=True,
            timeout=self._timeout,
            read_bufsize=1 << 18,  # 256KiB
            trust_env=_trust_env,
        )

        return self

    async def __aenter__(self) -> "Sessions":
        return await self.enter()

    async def close(self) -> None:
        close_coros = [self.app.close(), self.app_proto.close(), self.web.close()]
        if self.websocket and not self.websocket.closed:
            close_coros.append(self.websocket.close())

        await asyncio.gather(*close_coros, return_exceptions=True)
        await self._connector.close()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    @staticmethod
    def _wrap_form(forms: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """
        为form参数元组列表添加贴吧客户端签名

        Args:
            payload (list[tuple[str, str]]): form参数元组列表

        Returns:
            list[tuple[str, str]]: 签名后的form参数元组列表
        """

        raw_list = [f"{k}={v}" for k, v in forms]
        raw_list.append("tiebaclient!!!")
        raw_str = "".join(raw_list)

        md5 = hashlib.md5()
        md5.update(raw_str.encode('utf-8'))
        forms.append(('sign', md5.hexdigest()))

        return forms

    @staticmethod
    def _wrap_proto_bytes(req_bytes: bytes) -> aiohttp.MultipartWriter:
        """
        将req_bytes封装为贴吧客户端专用的aiohttp.MultipartWriter

        Args:
            req_bytes (bytes): protobuf序列化后的二进制数据

        Returns:
            aiohttp.MultipartWriter: 只可用于贴吧客户端
        """

        writer = aiohttp.MultipartWriter('form-data', boundary=f"*-6723-28094-46917-{random.randint(0,9)}")
        payload_headers = {
            aiohttp.hdrs.CONTENT_DISPOSITION: aiohttp.helpers.content_disposition_header(
                'form-data', name='data', filename='file'
            )
        }
        payload = aiohttp.BytesPayload(req_bytes, content_type='', headers=payload_headers)
        writer.append_payload(payload)

        # 删除无用参数
        writer._parts[0][0]._headers.popone(aiohttp.hdrs.CONTENT_TYPE)
        writer._parts[0][0]._headers.popone(aiohttp.hdrs.CONTENT_LENGTH)

        return writer

    @property
    def ws_password(self) -> bytes:
        if self._ws_password is None:
            self._ws_password = random.randbytes(36)

        return self._ws_password

    @property
    def ws_aes_chiper(self):
        if self._ws_aes_chiper is None:
            salt = b'\xa4\x0b\xc8\x34\xd6\x95\xf3\x13'
            ws_secret_key = hashlib.pbkdf2_hmac('sha1', self.ws_password, salt, 5, 32)
            self._ws_aes_chiper = AES.new(ws_secret_key, AES.MODE_ECB)

        return self._ws_aes_chiper

    def _wrap_ws_bytes(self, ws_bytes: bytes, cmd: int = 0, need_gzip: bool = True, need_encrypt: bool = True) -> bytes:
        """
        对ws_bytes进行封装

        Args:
            ws_bytes (bytes): 待发送的websocket数据
            cmd (int, optional): cmd代号. Defaults to 0.
            need_gzip (bool, optional): 是否需要gzip压缩. Defaults to False.
            need_encrypt (bool, optional): 是否需要aes加密. Defaults to False.

        Returns:
            bytes: 封装后的websocket数据
        """

        if need_gzip:
            ws_bytes = gzip.compress(ws_bytes, 5)

        if need_encrypt:
            pad_num = AES.block_size - (len(ws_bytes) % AES.block_size)
            ws_bytes += pad_num.to_bytes(1, 'little') * pad_num
            ws_bytes = self.ws_aes_chiper.encrypt(ws_bytes)

        flag = 0x08 | (need_gzip << 7) | (need_encrypt << 6)
        ws_bytes = b''.join(
            [
                flag.to_bytes(1, 'big'),
                cmd.to_bytes(4, 'big'),
                random.randbytes(4),
                ws_bytes,
            ]
        )

        return ws_bytes

    def _unwrap_ws_bytes(self, ws_bytes: bytes) -> bytes:
        """
        对ws_bytes进行解封装

        Args:
            ws_bytes (bytes): 接收到的websocket数据

        Returns:
            bytes: 解封装后的websocket数据
        """

        flag = ws_bytes[0]
        ws_bytes = ws_bytes[9:]

        if flag & 0b10000000:
            ws_bytes = self.ws_aes_chiper.decrypt(ws_bytes)
            ws_bytes = ws_bytes.rstrip(ws_bytes[-2:-1])
        if flag & 0b01000000:
            ws_bytes = gzip.decompress(ws_bytes)

        return ws_bytes

    async def create_websocket(self, heartbeat: float | None = None) -> bool:
        """
        建立weboscket连接

        Args:
            heartbeat (float | None, optional): 是否定时ping. Defaults to None.

        Returns:
            bool: 连接是否成功
        """

        if self._app_websocket is None:
            await self.enter()

        try:
            self.websocket = await self._app_websocket._ws_connect(
                "ws://im.tieba.baidu.com:8000", heartbeat=heartbeat, ssl=False
            )

        except Exception as err:
            LOG.warning(f"Failed to create websocket. reason:{err}")
            return False

        return True


class Browser(object):
    """
    贴吧浏览、参数获取等API的封装

    Args:
        BDUSS_key (str | None): 用于从config.json中提取BDUSS. Defaults to None.
    """

    __slots__ = ['BDUSS_key', 'sessions', '_tbs']

    fid_dict: dict[str, int] = {}

    def __init__(self, BDUSS_key: str | None = None) -> None:
        self.BDUSS_key = BDUSS_key
        self.sessions = Sessions(BDUSS_key)
        self._tbs: str = ''

    async def enter(self) -> "Browser":
        await self.sessions.enter()
        return self

    async def __aenter__(self) -> "Browser":
        return await self.enter()

    async def close(self) -> None:
        await self.sessions.close()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def get_websocket(self) -> aiohttp.ClientWebSocketResponse:
        """
        获取weboscket连接对象并发送初始化信息

        Returns:
            aiohttp.ClientWebSocketResponse: websocket连接对象
        """

        pub_key_bytes = base64.b64decode(
            "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwQpwBZxXJV/JVRF/uNfyMSdu7YWwRNLM8+2xbniGp2iIQHOikPpTYQjlQgMi1uvq1kZpJ32rHo3hkwjy2l0lFwr3u4Hk2Wk7vnsqYQjAlYlK0TCzjpmiI+OiPOUNVtbWHQiLiVqFtzvpvi4AU7C1iKGvc/4IS45WjHxeScHhnZZ7njS4S1UgNP/GflRIbzgbBhyZ9kEW5/OO5YfG1fy6r4KSlDJw4o/mw5XhftyIpL+5ZBVBC6E1EIiP/dd9AbK62VV1PByfPMHMixpxI3GM2qwcmFsXcCcgvUXJBa9k6zP8dDQ3csCM2QNT+CQAOxthjtp/TFWaD7MzOdsIYb3THwIDAQAB".encode(
                'ascii'
            )
        )
        pub_key = RSA.import_key(pub_key_bytes)
        rsa_chiper = PKCS1_v1_5.new(pub_key)

        data_proto = UpdateClientInfoReqIdl_pb2.UpdateClientInfoReqIdl.DataReq()
        data_proto.bduss = self.sessions.BDUSS
        data_proto.device = """{"subapp_type":"mini","_client_version":"9.1.0.0","pversion":"1.0.3","_msg_status":"1","_phone_imei":"000000000000000","from":"1021099l","cuid_galaxy2":"132D741FDA2C7D06A1BF9D63F213B453|0","model":"LIO-AN00","_client_type":"2"}"""
        data_proto.secretKey = rsa_chiper.encrypt(self.sessions.ws_password)
        req_proto = UpdateClientInfoReqIdl_pb2.UpdateClientInfoReqIdl()
        req_proto.data.CopyFrom(data_proto)
        req_proto.cuid = "baidutiebaapp4b825a46-779d-4004-a264-006433001684|com.baidu.tieba_mini9.1.0.0"

        websocket = self.sessions.websocket

        try:
            if websocket is None or websocket.closed:
                await self.sessions.create_websocket()
                websocket = self.sessions.websocket

            await websocket.send_bytes(
                self.sessions._wrap_ws_bytes(
                    req_proto.SerializeToString(), cmd=1001, need_gzip=False, need_encrypt=False
                )
            )

            res_proto = UpdateClientInfoResIdl_pb2.UpdateClientInfoResIdl()
            res_bytes = (await websocket.receive(timeout=5)).data
            res_proto.ParseFromString(self.sessions._unwrap_ws_bytes(res_bytes))
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

        except Exception as err:
            LOG.warning(f"Failed to create tieba-websocket. reason{err}")

        return websocket

    async def get_tbs(self) -> str:
        """
        获取贴吧反csrf校验码tbs

        Returns:
            str: 贴吧反csrf校验码tbs
        """

        if not self._tbs:
            await self.get_self_info()

        return self._tbs

    async def get_fid(self, fname: str) -> int:
        """
        通过贴吧名获取forum_id

        Args:
            fname (str): 贴吧名

        Returns:
            int: 该贴吧的forum_id
        """

        if fid := self.fid_dict.get(fname, 0):
            return fid

        try:
            res = await self.sessions.web.get(
                "http://tieba.baidu.com/f/commit/share/fnameShareApi",
                params={
                    'fname': fname,
                    'ie': 'utf-8',
                },
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['no']):
                raise ValueError(res_json['error'])

            if fid := int(res_json['data']['fid']):
                self.fid_dict[fname] = fid

        except Exception as err:
            LOG.warning(f"Failed to get fid of {fname}. reason:{err}")
            fid = 0

        return fid

    async def get_user_info(self, _id: str | int) -> UserInfo:
        """
        补全完整版用户信息

        Args:
            _id (str | int): 用户id user_id/user_name/portrait

        Returns:
            UserInfo: 完整版用户信息
        """

        user = UserInfo(_id)
        if user.user_id:
            return await self._user_id2user_info(user)
        else:
            return await self._id2user_info(user)

    async def get_basic_user_info(self, _id: str | int) -> BasicUserInfo:
        """
        补全简略版用户信息

        Args:
            _id (str | int): 用户id user_id/user_name/portrait

        Returns:
            BasicUserInfo: 简略版用户信息 仅保证包含user_name/portrait/user_id
        """

        user = BasicUserInfo(_id)
        if user.user_id:
            return await self._user_id2basic_user_info(user)
        elif user.user_name:
            return await self._user_name2basic_user_info(user)
        else:
            return await self._id2basic_user_info(user)

    async def _id2user_info(self, user: UserInfo) -> UserInfo:
        """
        通过用户名或昵称或portrait补全完整版用户信息

        Args:
            user (UserInfo): 待补全的用户信息

        Returns:
            UserInfo: 完整版用户信息
        """

        try:
            res = await self.sessions.web.get(
                "https://tieba.baidu.com/home/get/panel",
                params={
                    'id': user.portrait,
                    'un': user.user_name or user.nick_name,
                },
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['no']):
                raise ValueError(res_json['error'])

            user_dict: dict = res_json['data']
            match user_dict['sex']:
                case 'male':
                    gender = 1
                case 'female':
                    gender = 2
                case _:
                    gender = 0

            user.user_name = user_dict['name']
            user.nick_name = user_dict['show_nickname']
            user.portrait = user_dict['portrait']
            user.user_id = user_dict['id']
            user.gender = gender
            user.is_vip = int(vip_dict['v_status']) if (vip_dict := user_dict['vipInfo']) else False

        except Exception as err:
            LOG.warning(f"Failed to get UserInfo of {user.log_name}. reason:{err}")
            user = UserInfo()

        return user

    async def _id2basic_user_info(self, user: BasicUserInfo) -> BasicUserInfo:
        """
        通过用户名或昵称或portrait补全简略版用户信息

        Args:
            user (BasicUserInfo): 待补全的用户信息

        Returns:
            BasicUserInfo: 简略版用户信息 仅保证包含user_name/portrait/user_id
        """

        try:
            res = await self.sessions.web.get(
                "https://tieba.baidu.com/home/get/panel",
                params={
                    'id': user.portrait,
                    'un': user.user_name or user.nick_name,
                },
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['no']):
                raise ValueError(res_json['error'])

            user_dict = res_json['data']
            user.user_name = user_dict['name']
            user.nick_name = user_dict['show_nickname']
            user.portrait = user_dict['portrait']
            user.user_id = user_dict['id']

        except Exception as err:
            LOG.warning(f"Failed to get BasicUserInfo of {user.log_name}. reason:{err}")
            user = UserInfo()

        return user

    async def _user_name2basic_user_info(self, user: BasicUserInfo) -> BasicUserInfo:
        """
        通过用户名补全简略版用户信息

        Args:
            user (BasicUserInfo): 待补全的用户信息

        Returns:
            BasicUserInfo: 简略版用户信息 仅保证包含user_name/portrait/user_id
        """

        params = {
            'un': user.user_name,
            'ie': 'utf-8',
        }

        try:
            res = await self.sessions.web.get("http://tieba.baidu.com/i/sys/user_json", params=params)

            text = await res.text(encoding='utf-8', errors='ignore')
            res_json = json.loads(text)
            if not res_json:
                raise ValueError("empty response")

            user_dict = res_json['creator']
            user.user_id = user_dict['id']
            user.portrait = user_dict['portrait']

        except Exception as err:
            LOG.warning(f"Failed to get BasicUserInfo of {user.user_name}. reason:{err}")
            user = BasicUserInfo()

        return user

    async def _user_id2user_info(self, user: UserInfo) -> UserInfo:
        """
        通过user_id补全用户信息

        Args:
            user (UserInfo): 待补全的用户信息

        Returns:
            UserInfo: 完整版用户信息
        """

        common_proto = CommonReq_pb2.CommonReq()
        data_proto = GetUserInfoReqIdl_pb2.GetUserInfoReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        data_proto.uid = user.user_id
        req_proto = GetUserInfoReqIdl_pb2.GetUserInfoReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/u/user/getuserinfo?cmd=303024",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = GetUserInfoResIdl_pb2.GetUserInfoResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            user_proto = res_proto.data.user
            user = UserInfo(user_proto=user_proto)

        except Exception as err:
            LOG.warning(f"Failed to get UserInfo of {user.user_id}. reason:{err}")
            user = UserInfo()

        return user

    async def _user_id2basic_user_info(self, user: BasicUserInfo) -> BasicUserInfo:
        """
        通过user_id补全简略版用户信息

        Args:
            user (BasicUserInfo): 待补全的用户信息

        Returns:
            BasicUserInfo: 简略版用户信息 仅保证包含user_name/portrait/user_id
        """

        try:
            res = await self.sessions.web.get(
                "http://tieba.baidu.com/im/pcmsg/query/getUserInfo", params={'chatUid': user.user_id}
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['errno']):
                raise ValueError(res_json['errmsg'])

            user_dict = res_json['chatUser']
            user.user_name = user_dict['uname']
            user.portrait = user_dict['portrait']

        except Exception as err:
            LOG.warning(f"Failed to get BasicUserInfo of {user.user_id}. reason:{err}")
            user = BasicUserInfo()

        return user

    async def tieba_uid2user_info(self, tieba_uid: int) -> UserInfo:
        """
        通过tieba_uid补全用户信息

        Args:
            tieba_uid (int): 新版tieba_uid 请注意与旧版user_id的区别

        Returns:
            UserInfo: 完整版用户信息
        """

        common_proto = CommonReq_pb2.CommonReq()
        data_proto = GetUserByTiebaUidReqIdl_pb2.GetUserByTiebaUidReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        data_proto.tieba_uid = str(tieba_uid)
        req_proto = GetUserByTiebaUidReqIdl_pb2.GetUserByTiebaUidReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/u/user/getUserByTiebaUid?cmd=309702",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = GetUserByTiebaUidResIdl_pb2.GetUserByTiebaUidResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            user_proto = res_proto.data.user
            user = UserInfo(user_proto=user_proto)

        except Exception as err:
            LOG.warning(f"Failed to get UserInfo of {tieba_uid}. reason:{err}")
            user = UserInfo()

        return user

    async def get_threads(self, fname: str, pn: int = 1, sort: int = 5, is_good: bool = False) -> Threads:
        """
        获取首页帖子

        Args:
            fname (str): 贴吧名
            pn (int, optional): 页码. Defaults to 1.
            sort (int, optional): 排序方式 对于有热门分区的贴吧0是热门排序1是按发布时间2报错34都是热门排序>=5是按回复时间 \
                对于无热门分区的贴吧0是按回复时间1是按发布时间2报错>=3是按回复时间. Defaults to 5.
            is_good (bool, optional): True为获取精品区帖子 False为获取普通区帖子. Defaults to False.

        Returns:
            Threads: 帖子列表
        """

        common_proto = CommonReq_pb2.CommonReq()
        common_proto._client_version = '12.12.1.0'
        data_proto = FrsPageReqIdl_pb2.FrsPageReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        data_proto.kw = fname
        data_proto.pn = pn
        data_proto.rn = 30
        data_proto.is_good = is_good
        data_proto.q_type = 2
        data_proto.sort_type = sort
        req_proto = FrsPageReqIdl_pb2.FrsPageReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/f/frs/page?cmd=301001",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = FrsPageResIdl_pb2.FrsPageResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            threads = Threads(res_proto)

        except Exception as err:
            LOG.warning(f"Failed to get threads of {fname}. reason:{err}")
            threads = Threads()

        return threads

    async def get_posts(
        self,
        tid: int,
        pn: int = 1,
        rn: int = 30,
        sort: int = 0,
        only_thread_author: bool = False,
        with_comments: bool = False,
        comment_sort_by_agree: bool = True,
        comment_rn: int = 10,
        is_fold: bool = False,
    ) -> Posts:
        """
        获取主题帖内回复

        Args:
            tid (int): 所在主题帖tid
            pn (int, optional): 页码. Defaults to 1.
            rn (int, optional): 请求的条目数. Defaults to 30.
            sort (int, optional): 0则按时间顺序请求 1则按时间倒序请求 2则按热门序请求. Defaults to 0.
            only_thread_author (bool, optional): True则只看楼主 False则请求全部. Defaults to False.
            with_comments (bool, optional): True则同时请求高赞楼中楼 False则返回的Posts.comments为空. Defaults to False.
            comment_sort_by_agree (bool, optional): True则楼中楼按点赞数顺序 False则楼中楼按时间顺序. Defaults to True.
            comment_rn (int, optional): 请求的楼中楼数量. Defaults to 10.
            is_fold (bool, optional): 是否请求被折叠的回复. Defaults to False.

        Returns:
            Posts: 回复列表
        """

        common_proto = CommonReq_pb2.CommonReq()
        common_proto._client_version = '12.12.1.0'
        data_proto = PbPageReqIdl_pb2.PbPageReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        data_proto.kz = tid
        data_proto.pn = pn
        data_proto.rn = rn if rn > 1 else 2
        data_proto.q_type = 2
        data_proto.r = sort
        data_proto.lz = only_thread_author
        data_proto.is_fold_comment_req = is_fold
        if with_comments:
            data_proto.with_floor = with_comments
            data_proto.floor_sort_type = comment_sort_by_agree
            data_proto.floor_rn = comment_rn
        req_proto = PbPageReqIdl_pb2.PbPageReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/f/pb/page?cmd=302001",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = PbPageResIdl_pb2.PbPageResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            posts = Posts(res_proto)

        except Exception as err:
            LOG.warning(f"Failed to get posts of {tid}. reason:{err}")
            posts = Posts()

        return posts

    async def get_comments(self, tid: int, pid: int, pn: int = 1, is_floor: bool = False) -> Comments:
        """
        获取楼中楼回复

        Args:
            tid (int): 所在主题帖tid
            pid (int): 所在回复pid或楼中楼pid
            pn (int, optional): 页码. Defaults to 1.
            is_floor (bool, optional): pid是否指向楼中楼. Defaults to False.

        Returns:
            Comments: 楼中楼列表
        """

        common_proto = CommonReq_pb2.CommonReq()
        common_proto._client_version = '12.12.1.0'
        data_proto = PbFloorReqIdl_pb2.PbFloorReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        data_proto.kz = tid
        if is_floor:
            data_proto.spid = pid
        else:
            data_proto.pid = pid
        data_proto.pn = pn
        req_proto = PbFloorReqIdl_pb2.PbFloorReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/f/pb/floor?cmd=302002",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = PbFloorResIdl_pb2.PbFloorResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            comments = Comments(res_proto)

        except Exception as err:
            LOG.warning(f"Failed to get comments of {pid} in {tid}. reason:{err}")
            comments = Comments()

        return comments

    async def block(self, fname: str, user: BasicUserInfo, day: int, reason: str = '') -> bool:
        """
        封禁用户 支持小吧主/语音小编封3/10天

        Args:
            fname (str): 贴吧名
            user (BasicUserInfo): 待封禁用户信息
            day (int): 封禁天数
            reason (str, optional): 封禁理由. Defaults to ''.

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('day', day),
            ('fid', await self.get_fid(fname)),
            ('nick_name', user.show_name),
            ('ntn', 'banid'),
            ('portrait', user.portrait),
            ('reason', reason),
            ('tbs', await self.get_tbs()),
            ('un', user.user_name),
            ('word', fname),
            ('z', 672328094),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/commitprison", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to block {user.log_name} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully blocked {user.log_name} in {fname} for {day} days")
        return True

    async def unblock(self, fname: str, user: BasicUserInfo) -> bool:
        """
        解封用户

        Args:
            fname (str): 贴吧名
            user (BasicUserInfo): 基本用户信息

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('fn', fname),
            ('fid', await self.get_fid(fname)),
            ('block_un', user.user_name),
            ('block_uid', user.user_id),
            ('block_nickname', user.nick_name),
            ('tbs', await self.get_tbs()),
        ]

        try:
            res = await self.sessions.web.post("https://tieba.baidu.com/mo/q/bawublockclear", data=payload)

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['no']):
                raise ValueError(res_json['error'])

        except Exception as err:
            LOG.warning(f"Failed to unblock {user.log_name} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully unblocked {user.log_name} in {fname}")
        return True

    async def hide_thread(self, fname: str, tid: int) -> bool:
        """
        屏蔽主题帖

        Args:
            fname (str): 帖子所在的贴吧名
            tid (int): 待屏蔽的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        return await self._del_thread(fname, tid, is_hide=True)

    async def del_thread(self, fname: str, tid: int) -> bool:
        """
        删除主题帖

        Args:
            fname (str): 帖子所在的贴吧名
            tid (int): 待删除的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        return await self._del_thread(fname, tid, is_hide=False)

    async def _del_thread(self, fname: str, tid: int, is_hide: bool = False) -> bool:
        """
        删除/屏蔽主题帖

        Args:
            fname (str): 帖子所在的贴吧名
            tid (int): 待删除/屏蔽的主题帖tid
            is_hide (bool, optional): True则屏蔽帖 False则删除帖. Defaults to False.

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('fid', await self.get_fid(fname)),
            ('is_frs_mask', int(is_hide)),
            ('tbs', await self.get_tbs()),
            ('z', tid),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/delthread", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to delete thread tid:{tid} is_hide:{is_hide} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully deleted thread tid:{tid} is_hide:{is_hide} in {fname}")
        return True

    async def del_post(self, fname: str, tid: int, pid: int) -> bool:
        """
        删除回复

        Args:
            fname (str): 帖子所在的贴吧名
            tid (int): 回复所在的主题帖tid
            pid (int): 待删除的回复pid

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('fid', await self.get_fid(fname)),
            ('pid', pid),
            ('tbs', await self.get_tbs()),
            ('z', tid),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/delpost", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to delete post {pid} in {tid} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully deleted post {pid} in {tid} in {fname}")
        return True

    async def unhide_thread(self, fname, tid: int) -> bool:
        """
        解除主题帖屏蔽

        Args:
            fname (str): 帖子所在的贴吧名
            tid (int, optional): 待解除屏蔽的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        return await self._recover(fname, tid=tid, is_hide=True)

    async def recover_thread(self, fname, tid: int) -> bool:
        """
        恢复主题帖

        Args:
            fname (str): 帖子所在的贴吧名
            tid (int, optional): 待恢复的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        return await self._recover(fname, tid=tid, is_hide=False)

    async def recover_post(self, fname, pid: int) -> bool:
        """
        恢复主题帖

        Args:
            fname (str): 帖子所在的贴吧名
            pid (int, optional): 待恢复的回复pid

        Returns:
            bool: 操作是否成功
        """

        return await self._recover(fname, pid=pid, is_hide=False)

    async def _recover(self, fname, tid: int = 0, pid: int = 0, is_hide: bool = False) -> bool:
        """
        恢复帖子

        Args:
            fname (str): 帖子所在的贴吧名
            tid (int, optional): 待恢复的主题帖tid. Defaults to 0.
            pid (int, optional): 待恢复的回复pid. Defaults to 0.
            is_hide (bool, optional): True则取消屏蔽主题帖 False则恢复删帖. Defaults to False.

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('fn', fname),
            ('fid', await self.get_fid(fname)),
            ('tid_list[]', tid),
            ('pid_list[]', pid),
            ('type_list[]', 1 if pid else 0),
            ('is_frs_mask_list[]', int(is_hide)),
        ]

        try:
            res = await self.sessions.web.post("https://tieba.baidu.com/mo/q/bawurecoverthread", data=payload)

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['no']):
                raise ValueError(res_json['error'])

        except Exception as err:
            LOG.warning(f"Failed to recover tid:{tid} pid:{pid} hide:{is_hide} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully recovered tid:{tid} pid:{pid} hide:{is_hide} in {fname}")
        return True

    async def move(self, fname: str, tid: int, to_tab_id: int, from_tab_id: int = 0) -> bool:
        """
        将主题帖移动至另一分区

        Args:
            fname (str): 帖子所在贴吧名
            tid (int): 待移动的主题帖tid
            to_tab_id (int): 目标分区id
            from_tab_id (int, optional): 来源分区id 默认为0即无分区. Defaults to 0.

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('_client_version', '12.12.1.0'),
            ('forum_id', await self.get_fid(fname)),
            ('tbs', await self.get_tbs()),
            (
                'threads',
                str([{'thread_id', tid, 'from_tab_id', from_tab_id, 'to_tab_id', to_tab_id}]).replace('\'', '"'),
            ),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/moveTabThread", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to move {tid} to tab:{to_tab_id} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully moved {tid} to tab:{to_tab_id} in {fname}")
        return True

    async def recommend(self, fname: str, tid: int) -> bool:
        """
        大吧主首页推荐

        Args:
            fname (str): 帖子所在贴吧名
            tid (int): 待推荐的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('forum_id', await self.get_fid(fname)),
            ('thread_id', tid),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/pushRecomToPersonalized", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])
            if int(res_json['data']['is_push_success']) != 1:
                raise ValueError(res_json['data']['msg'])

        except Exception as err:
            LOG.warning(f"Failed to recommend {tid} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully recommended {tid} in {fname}")
        return True

    async def good(self, fname: str, tid: int, cname: str = '') -> bool:
        """
        加精主题帖

        Args:
            fname (str): 帖子所在贴吧名
            tid (int): 待加精的主题帖tid
            cname (str, optional): 待添加的精华分区名称 默认为''即不分区. Defaults to ''.

        Returns:
            bool: 操作是否成功
        """

        async def _cname2cid() -> int:
            """
            由加精分区名cname获取cid

            Closure Args:
                fname (str): 帖子所在贴吧名
                cname (str, optional): 待添加的精华分区名称 默认为''即不分区. Defaults to ''.

            Returns:
                int: cname对应的分区id
            """

            payload = [
                ('BDUSS', self.sessions.BDUSS),
                ('word', fname),
            ]

            try:
                res = await self.sessions.app.post(
                    "http://c.tieba.baidu.com/c/c/bawu/goodlist", data=self.sessions._wrap_form(payload)
                )

                res_json: dict = await res.json(encoding='utf-8', content_type=None)
                if int(res_json['error_code']):
                    raise ValueError(res_json['error_msg'])

                cid = 0
                for item in res_json['cates']:
                    if cname == item['class_name']:
                        cid = int(item['class_id'])
                        break

            except Exception as err:
                LOG.warning(f"Failed to get cid of {cname} in {fname}. reason:{err}")
                return 0

            return cid

        async def _good(cid: int = 0) -> bool:
            """
            加精主题帖

            Args:
                cid (int, optional): 将主题帖加到cid对应的精华分区 cid默认为0即不分区. Defaults to 0.

            Closure Args:
                fname (str): 帖子所在贴吧名

            Returns:
                bool: 操作是否成功
            """

            payload = [
                ('BDUSS', self.sessions.BDUSS),
                ('cid', cid),
                ('fid', await self.get_fid(fname)),
                ('ntn', 'set'),
                ('tbs', await self.get_tbs()),
                ('word', fname),
                ('z', tid),
            ]

            try:
                res = await self.sessions.app.post(
                    "http://c.tieba.baidu.com/c/c/bawu/commitgood", data=self.sessions._wrap_form(payload)
                )

                res_json: dict = await res.json(encoding='utf-8', content_type=None)
                if int(res_json['error_code']):
                    raise ValueError(res_json['error_msg'])

            except Exception as err:
                LOG.warning(f"Failed to add {tid} to good_list:{cname} in {fname}. reason:{err}")
                return False

            LOG.info(f"Successfully added {tid} to good_list:{cname} in {fname}")
            return True

        return await _good(await _cname2cid())

    async def ungood(self, fname: str, tid: int) -> bool:
        """
        撤精主题帖

        Args:
            fname (str): 帖子所在贴吧名
            tid (int): 待撤精的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('fid', await self.get_fid(fname)),
            ('tbs', await self.get_tbs()),
            ('word', fname),
            ('z', tid),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/commitgood", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to remove {tid} from good_list in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully removed {tid} from good_list in {fname}")
        return True

    async def top(self, fname: str, tid: int) -> bool:
        """
        置顶主题帖

        Args:
            fname (str): 帖子所在贴吧名
            tid (int): 待置顶的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('fid', await self.get_fid(fname)),
            ('ntn', 'set'),
            ('tbs', await self.get_tbs()),
            ('word', fname),
            ('z', tid),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/committop", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to add {tid} to top_list in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully added {tid} to top_list in {fname}")
        return True

    async def untop(self, fname: str, tid: int) -> bool:
        """
        撤销置顶主题帖

        Args:
            fname (str): 帖子所在贴吧名
            tid (int): 待撤销置顶的主题帖tid

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('fid', await self.get_fid(fname)),
            ('tbs', await self.get_tbs()),
            ('word', fname),
            ('z', tid),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/bawu/committop", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to remove {tid} from top_list in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully removed {tid} from top_list in {fname}")
        return True

    async def get_recover_list(
        self, fname: str, pn: int = 1, name: str = ''
    ) -> tuple[list[tuple[int, int, bool]], bool]:
        """
        获取pn页的待恢复帖子列表

        Args:
            fname (str): 贴吧名
            pn (int, optional): 页码. Defaults to 1.
            name (str, optional): 通过被删帖作者的用户名/昵称查询 默认为空即查询全部. Defaults to ''.

        Returns:
            tuple[list[tuple[int, int, bool]], bool]: list[tid,pid,是否为屏蔽], 是否还有下一页
        """

        params = {
            'fn': fname,
            'fid': await self.get_fid(fname),
            'word': name,
            'is_ajax': 1,
            'pn': pn,
        }

        try:
            res = await self.sessions.web.get("https://tieba.baidu.com/mo/q/bawurecover", params=params)

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['no']):
                raise ValueError(res_json['error'])

            data = res_json['data']
            soup = BeautifulSoup(data['content'], 'lxml')
            items = soup.find_all('a', class_='recover_list_item_btn')

        except Exception as err:
            LOG.warning(f"Failed to get recover_list of {fname} pn:{pn}. reason:{err}")
            res_list = []
            has_more = False

        else:

            def _parse_item(item):
                tid = int(item['attr-tid'])
                pid = int(item['attr-pid'])
                is_frs_mask = bool(int(item['attr-isfrsmask']))

                return tid, pid, is_frs_mask

            res_list = [_parse_item(item) for item in items]
            has_more = data['page']['have_next']

        return res_list, has_more

    async def get_black_list(self, fname: str, pn: int = 1) -> tuple[list[BasicUserInfo], bool]:
        """
        获取pn页的黑名单

        Args:
            fname (str): 贴吧名
            pn (int, optional): 页码. Defaults to 1.

        Returns:
            tuple[list[BasicUserInfo], bool]: list[基本用户信息], 是否还有下一页
        """

        try:
            res = await self.sessions.web.get(
                "http://tieba.baidu.com/bawu2/platform/listBlackUser",
                params={
                    'word': fname,
                    'pn': pn,
                },
            )

            soup = BeautifulSoup(await res.text(), 'lxml')
            items = soup.find_all('td', class_='left_cell')

        except Exception as err:
            LOG.warning(f"Failed to get black_list of {fname} pn:{pn}. reason:{err}")
            res_list = []
            has_more = False

        else:

            def _parse_item(item):
                user_info_item = item.previous_sibling.input
                user = BasicUserInfo()
                user.user_name = user_info_item['data-user-name']
                user.user_id = int(user_info_item['data-user-id'])
                user.portrait = item.a.img['src'][43:]
                return user

            res_list = [_parse_item(item) for item in items]
            has_more = len(items) == 15

        return res_list, has_more

    async def blacklist_add(self, fname: str, user: BasicUserInfo) -> bool:
        """
        添加贴吧黑名单

        Args:
            fname (str): 贴吧名
            user (BasicUserInfo): 基本用户信息

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('tbs', await self.get_tbs()),
            ('user_id', user.user_id),
            ('word', fname),
            ('ie', 'utf-8'),
        ]

        try:
            res = await self.sessions.web.post("http://tieba.baidu.com/bawu2/platform/addBlack", data=payload)

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['errno']):
                raise ValueError(res_json['errmsg'])

        except Exception as err:
            LOG.warning(f"Failed to add {user.log_name} to black_list in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully added {user.log_name} to black_list in {fname}")
        return True

    async def blacklist_del(self, fname: str, user: BasicUserInfo) -> bool:
        """
        移出贴吧黑名单

        Args:
            fname (str): 贴吧名
            user (BasicUserInfo): 基本用户信息

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('word', fname),
            ('tbs', await self.get_tbs()),
            ('list[]', user.user_id),
            ('ie', 'utf-8'),
        ]

        try:
            res = await self.sessions.web.post("http://tieba.baidu.com/bawu2/platform/cancelBlack", data=payload)

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['errno']):
                raise ValueError(res_json['errmsg'])

        except Exception as err:
            LOG.warning(f"Failed to remove {user.log_name} from black_list in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully removed {user.log_name} from black_list in {fname}")
        return True

    async def handle_unblock_appeal(self, fname: str, appeal_id: int, refuse: bool = True) -> bool:
        """
        拒绝或通过解封申诉

        Args:
            fname (str): 贴吧名
            appeal_id (int): 申诉请求的appeal_id
            refuse (bool, optional): True则拒绝申诉 False则接受申诉. Defaults to True.

        Closure Args:
            fname (str): 贴吧名

        Returns:
            bool: 操作是否成功
        """

        payload = [
            ('fn', fname),
            ('fid', await self.get_fid(fname)),
            ('status', 2 if refuse else 1),
            ('refuse_reason', 'auto refuse'),
            ('appeal_id', appeal_id),
        ]

        try:
            res = await self.sessions.web.post("https://tieba.baidu.com/mo/q/bawuappealhandle", data=payload)

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['no']):
                raise ValueError(res_json['error'])

        except Exception as err:
            LOG.warning(f"Failed to handle {appeal_id} in {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully handled {appeal_id} in {fname}. refuse:{refuse}")
        return True

    async def get_unblock_appeal_list(self, fname: str) -> list[int]:
        """
        获取第1页的申诉请求列表

        Args:
            fname (str): 贴吧名

        Returns:
            list[int]: 申诉请求的appeal_id的列表
        """

        params = {
            'fn': fname,
            'fid': await self.get_fid(fname),
            'is_ajax': 1,
            'pn': 1,
        }

        try:
            res = await self.sessions.web.get("https://tieba.baidu.com/mo/q/bawuappeal", params=params)

            text = await res.text(encoding='utf-8')

            res_list = [int(item.group(1)) for item in re.finditer('aid=(\d+)', text)]

        except Exception as err:
            LOG.warning(f"Failed to get appeal_list of {fname}. reason:{err}")
            res_list = []

        return res_list

    async def get_image(self, img_url: str) -> np.ndarray | None:
        """
        从链接获取jpg/png图像

        Args:
            img_url (str): 图像链接

        Returns:
            np.ndarray | None: 图像或None
        """

        try:
            res = await self.sessions.web.get(img_url)

            content = await res.content.read()
            img_type = res.content_type.removeprefix('image/')
            if img_type not in ['jpeg', 'png']:
                raise ValueError(f"Content-Type should be image/jpeg or image/png rather than {res.content_type}")

            image = cv.imdecode(np.frombuffer(content, np.uint8), cv.IMREAD_COLOR)

        except Exception as err:
            LOG.warning(f"Failed to get image {img_url}. reason:{err}")
            image = None

        return image

    async def get_self_info(self) -> BasicUserInfo:
        """
        获取本账号信息

        Returns:
            BasicUserInfo: 简略版用户信息 仅保证包含user_name/portrait/user_id
        """

        payload = [
            ('_client_version', '12.12.1.0'),
            ('bdusstoken', self.sessions.BDUSS),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/s/login", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            user_dict = res_json['user']
            user_proto = ParseDict(user_dict, User_pb2.User(), ignore_unknown_fields=True)
            user = BasicUserInfo(user_proto=user_proto)

            self._tbs = res_json['anti']['tbs']

        except Exception as err:
            LOG.warning(f"Failed to get UserInfo of current account. reason:{err}")
            user = BasicUserInfo()

        return user

    async def get_newmsg(self) -> dict[str, bool]:
        """
        获取消息通知

        Returns:
            dict[str, bool]: msg字典 value=True则表示有新内容
            {'fans': 新粉丝,
             'replyme': 新回复,
             'atme': 新@,
             'agree': 新赞同,
             'pletter': 新私信,
             'bookmark': 新收藏,
             'count': 新通知}
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/s/msg", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            msg = {key: bool(int(value)) for key, value in res_json['message'].items()}

        except Exception as err:
            LOG.warning(f"Failed to get new_msg reason:{err}")
            msg = {
                'fans': False,
                'replyme': False,
                'atme': False,
                'agree': False,
                'pletter': False,
                'bookmark': False,
                'count': False,
            }

        return msg

    async def get_replys(self) -> Replys:
        """
        获取回复信息

        Returns:
            Replys: 回复列表
        """

        common_proto = CommonReq_pb2.CommonReq()
        common_proto.BDUSS = self.sessions.BDUSS
        common_proto._client_version = '12.12.1.0'
        data_proto = ReplyMeReqIdl_pb2.ReplyMeReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        req_proto = ReplyMeReqIdl_pb2.ReplyMeReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/u/feed/replyme?cmd=303007",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = ReplyMeResIdl_pb2.ReplyMeResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            replys = Replys(res_proto)

        except Exception as err:
            LOG.warning(f"Failed to get replys reason:{err}")
            replys = Replys()

        return replys

    async def get_ats(self) -> Ats:
        """
        获取@信息

        Returns:
            Ats: at列表
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('_client_version', '12.12.1.0'),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/u/feed/atme", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', loads=JSON_DECODER.decode, content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            ats = Ats(res_json)

        except Exception as err:
            LOG.warning(f"Failed to get ats reason:{err}")
            ats = Ats()

        return ats

    async def get_homepage(self, _id: str | int) -> tuple[UserInfo, list[Thread]]:
        """
        获取用户个人页信息

        Args:
            _id (str | int): 用户id user_id/user_name/portrait

        Returns:
            tuple[UserInfo, list[Thread]]: 用户信息, list[帖子信息]
        """

        if not BasicUserInfo.is_portrait(_id):
            user = await self.get_basic_user_info(_id)
        else:
            user = BasicUserInfo(_id)

        payload = [
            ('_client_type', 2),  # 删除该字段会导致post_list为空
            ('_client_version', '12.12.1.0'),  # 删除该字段会导致post_list和dynamic_list为空
            ('friend_uid_portrait', user.portrait),
            ('need_post_count', 1),  # 删除该字段会导致无法获取发帖回帖数量
            # ('uid', user_id),  # 用该字段检查共同关注的吧
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/u/user/profile", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', loads=JSON_DECODER.decode, content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])
            if not res_json.__contains__('user'):
                raise ValueError("invalid params")

        except Exception as err:
            LOG.warning(f"Failed to get profile of {user.portrait}. reason:{err}")
            return UserInfo(), []

        user = UserInfo(user_proto=ParseDict(res_json['user'], User_pb2.User(), ignore_unknown_fields=True))

        def _parse_thread_dict(thread_dict: dict) -> Thread:
            thread_dict['fid'] = thread_dict.pop('forum_id', 0)
            thread_dict['id'] = thread_dict.pop('thread_id', 0)
            thread = Thread(ParseDict(thread_dict, ThreadInfo_pb2.ThreadInfo(), ignore_unknown_fields=True))
            thread.user = user
            return thread

        threads = [_parse_thread_dict(thread_dict) for thread_dict in res_json['post_list']]

        return user, threads

    async def search_post(
        self, fname: str, query: str, pn: int = 1, rn: int = 30, query_type: int = 0, only_thread: bool = False
    ) -> Searches:
        """
        贴吧搜索

        Args:
            fname (str): 贴吧名
            query (str): 查询文本
            pn (int, optional): 页码. Defaults to 1.
            rn (int, optional): 请求的条目数. Defaults to 30.
            query_type (int, optional): 查询模式 0为全部搜索结果并且app似乎不提供这一模式 1为app时间倒序 2为app相关性排序. Defaults to 0.
            only_thread (bool, optional): 是否仅查询主题帖. Defaults to False.

        Returns:
            Searches: 搜索结果列表
        """

        payload = [
            ('_client_version', '12.12.1.0'),
            ('kw', fname),
            ('only_thread', int(only_thread)),
            ('pn', pn),
            ('rn', rn),
            ('sm', query_type),
            ('word', query),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/s/searchpost", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', loads=JSON_DECODER.decode, content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            searches = Searches(res_json)

        except Exception as err:
            LOG.warning(f"Failed to search {query} in {fname}. reason:{err}")
            searches = Searches()

        return searches

    async def get_self_forum_list(self, pn: int = 1) -> tuple[list[tuple[str, int, int, int]], bool]:
        """
        获取第pn页的本人关注贴吧列表

        Args:
            pn (int, optional): 页码. Defaults to 1.

        Returns:
            tuple[list[tuple[str, int]], bool]: list[贴吧名, 贴吧id], 是否还有下一页
        """

        try:
            res = await self.sessions.web.get("https://tieba.baidu.com/mg/o/getForumHome", params={'pn': pn, 'rn': 200})

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['errno']):
                raise ValueError(res_json['errmsg'])

            forums: list[dict] = res_json['data']['like_forum']['list']
            res_list = [(forum['forum_name'], int(forum['forum_id'])) for forum in forums]
            has_more = len(forums) == 200

        except Exception as err:
            LOG.warning(f"Failed to get self_forum_list. reason:{err}")
            res_list = []
            has_more = False

        return res_list, has_more

    async def get_forum_list(self, _id: str | int) -> list[tuple[str, int, int, int]]:
        """
        获取用户关注贴吧列表

        Args:
            _id (str | int): 用户id user_id/user_name/portrait

        Returns:
            list[tuple[str, int, int, int]]: list[贴吧名, 贴吧id, 等级, 经验值]
        """

        if not UserInfo.is_user_id(_id):
            user = await self.get_basic_user_info(_id)
        else:
            user = BasicUserInfo(_id)

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('friend_uid', user.user_id),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/f/forum/like", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            forums: list[dict] = res_json.get('forum_list', [])

            res_list = [
                (forum['name'], int(forum['id']), int(forum['level_id']), int(forum['cur_score'])) for forum in forums
            ]

        except Exception as err:
            LOG.warning(f"Failed to get forum_list of {user.user_id}. reason:{err}")
            res_list = []

        return res_list

    async def get_forum_detail(self, fname: str = '', fid: int = 0) -> tuple[str, int, int]:
        """
        通过forum_id获取贴吧信息

        Args:
            fname (str, optional): 贴吧名. Defaults to ''.
            fid (int, optional): forum_id. Defaults to 0.

        Returns:
            tuple[str, int, int]: 该贴吧的贴吧名, 关注人数, 主题帖数
        """

        if not fid:
            fid = await self.get_fid(fname)

        payload = [
            ('_client_version', '12.12.1.0'),
            ('forum_id', fid),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/f/forum/getforumdetail", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            fname = res_json['forum_info']['forum_name']
            member_num = int(res_json['forum_info']['member_count'])
            thread_num = int(res_json['forum_info']['thread_count'])

        except Exception as err:
            LOG.warning(f"Failed to get forum_detail of {fid}. reason:{err}")
            fname = ''
            member_num = 0
            thread_num = 0

        return fname, member_num, thread_num

    async def get_bawu_dict(self, fname: str) -> dict[str, list[BasicUserInfo]]:
        """
        获取吧务信息

        Args:
            fname (str): 贴吧名

        Returns:
            dict[str, list[BasicUserInfo]]: {吧务类型: list[吧务基本用户信息]}
        """

        common_proto = CommonReq_pb2.CommonReq()
        common_proto._client_version = '12.12.1.0'
        data_proto = GetBawuInfoReqIdl_pb2.GetBawuInfoReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        data_proto.forum_id = await self.get_fid(fname)
        req_proto = GetBawuInfoReqIdl_pb2.GetBawuInfoReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/f/forum/getBawuInfo?cmd=301007",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = GetBawuInfoResIdl_pb2.GetBawuInfoResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            roledes_protos = res_proto.data.bawu_team_info.bawu_team_list
            bawu_dict = {
                roledes_proto.role_name: [
                    BasicUserInfo(user_proto=roleinfo_proto) for roleinfo_proto in roledes_proto.role_info
                ]
                for roledes_proto in roledes_protos
            }

        except Exception as err:
            LOG.warning(f"Failed to get adminlist reason: {err}")
            bawu_dict = {}

        return bawu_dict

    async def get_tab_map(self, fname: str) -> dict[str, int]:
        """
        获取分区名到分区id的映射字典

        Args:
            fname (str): 贴吧名

        Returns:
            dict[str, int]: {分区名:分区id}
        """

        common_proto = CommonReq_pb2.CommonReq()
        common_proto.BDUSS = self.sessions.BDUSS
        common_proto._client_version = '12.12.1.0'
        data_proto = SearchPostForumReqIdl_pb2.SearchPostForumReqIdl.DataReq()
        data_proto.common.CopyFrom(common_proto)
        data_proto.word = fname
        req_proto = SearchPostForumReqIdl_pb2.SearchPostForumReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            res = await self.sessions.app_proto.post(
                "http://c.tieba.baidu.com/c/f/forum/searchPostForum?cmd=309466",
                data=self.sessions._wrap_proto_bytes(req_proto.SerializeToString()),
            )

            res_proto = SearchPostForumResIdl_pb2.SearchPostForumResIdl()
            res_proto.ParseFromString(await res.content.read())
            if int(res_proto.error.errorno):
                raise ValueError(res_proto.error.errmsg)

            tab_map = {tab_proto.tab_name: tab_proto.tab_id for tab_proto in res_proto.data.exact_match.tab_info}

        except Exception as err:
            LOG.warning(f"Failed to get tab_map of {fname}. reason:{err}")
            tab_map = {}

        return tab_map

    async def get_recom_list(self, fname: str, pn: int = 1) -> tuple[list[tuple[Thread, int]], bool]:
        """
        获取pn页的大吧主推荐帖列表

        Args:
            fname (str): 贴吧名
            pn (int, optional): 页码. Defaults to 1.

        Returns:
            tuple[list[tuple[Thread, int]], bool]: list[被推荐帖子信息,新增浏览量], 是否还有下一页
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('_client_version', '12.12.1.0'),
            ('forum_id', await self.get_fid(fname)),
            ('pn', pn),
            ('rn', 30),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/f/bawu/getRecomThreadHistory", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', loads=JSON_DECODER.decode, content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to get recom_list of {fname}. reason:{err}")
            res_list = []
            has_more = False

        else:

            def _parse_data_dict(data_dict):
                thread_dict = data_dict['thread_list']
                thread = Thread(ParseDict(thread_dict, ThreadInfo_pb2.ThreadInfo(), ignore_unknown_fields=True))
                add_view = thread.view_num - int(data_dict['current_pv'])
                return thread, add_view

            res_list = [_parse_data_dict(data_dict) for data_dict in res_json['recom_thread_list']]
            has_more = bool(int(res_json['is_has_more']))

        return res_list, has_more

    async def get_recom_status(self, fname: str) -> tuple[int, int]:
        """
        获取大吧主推荐功能的月度配额状态

        Args:
            fname (str): 贴吧名

        Returns:
            tuple[int, int]: 本月总推荐配额, 本月已使用的推荐配额
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('_client_version', '12.12.1.0'),
            ('forum_id', await self.get_fid(fname)),
            ('pn', 1),
            ('rn', 0),
        ]

        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/f/bawu/getRecomThreadList", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            total_recom_num = int(res_json['total_recommend_num'])
            used_recom_num = int(res_json['used_recommend_num'])

        except Exception as err:
            LOG.warning(f"Failed to get recom_status of {fname}. reason:{err}")
            total_recom_num = 0
            used_recom_num = 0

        return total_recom_num, used_recom_num

    async def get_statistics(self, fname: str) -> dict[str, list[int]]:
        """
        获取吧务后台中最近29天的统计数据

        Args:
            fname (str): 贴吧名

        Returns:
            dict[str, list[int]]: {字段名:按时间顺序排列的统计数据}
            {'view': 浏览量,
             'thread': 主题帖数,
             'new_member': 新增关注数,
             'post': 回复数,
             'sign_ratio': 签到率,
             'average_time': 人均浏览时长,
             'average_times': 人均进吧次数,
             'recommend': 首页推荐数}
        """

        payload = [
            ('BDUSS', self.sessions.BDUSS),
            ('_client_version', '12.12.1.0'),
            ('forum_id', await self.get_fid(fname)),
        ]

        field_names = [
            'view',
            'thread',
            'new_member',
            'post',
            'sign_ratio',
            'average_time',
            'average_times',
            'recommend',
        ]
        try:
            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/f/forum/getforumdata", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

            data = res_json['data']
            stat = {
                field_name: [int(item['value']) for item in reversed(data_i['group'][1]['values'])]
                for field_name, data_i in zip(field_names, data)
            }

        except Exception as err:
            LOG.warning(f"Failed to get statistics of {fname}. reason:{err}")
            stat = {field_name: [] for field_name in field_names}

        return stat

    async def get_rank_list(self, fname: str, pn: int = 1) -> tuple[list[tuple[str, int, int, bool]], bool]:
        """
        获取pn页的贴吧等级排行榜

        Args:
            fname (str): 贴吧名
            pn (int, optional): 页码. Defaults to 1.

        Returns:
            tuple[list[tuple[str, int, int, bool]], bool]: list[用户名,等级,经验值,是否vip], 是否还有下一页
        """

        try:
            res = await self.sessions.web.get(
                "http://tieba.baidu.com/f/like/furank",
                params={
                    'kw': fname,
                    'pn': pn,
                    'ie': 'utf-8',
                },
            )

            soup = BeautifulSoup(await res.text(), 'lxml')
            items = soup.select('tr[class^=drl_list_item]')

        except Exception as err:
            LOG.warning(f"Failed to get rank_list of {fname} pn:{pn}. reason:{err}")
            res_list = []
            has_more = False

        else:

            def _parse_item(item):
                user_name_item = item.td.next_sibling
                user_name = user_name_item.text
                is_vip = 'drl_item_vip' in user_name_item.div['class']
                level_item = user_name_item.next_sibling
                # e.g. get level 16 from string "bg_lv16" by slicing [5:]
                level = int(level_item.div['class'][0][5:])
                exp_item = level_item.next_sibling
                exp = int(exp_item.text)

                return user_name, level, exp, is_vip

            res_list = [_parse_item(item) for item in items]
            has_more = len(items) == 20

        return res_list, has_more

    async def get_member_list(self, fname: str, pn: int = 1) -> tuple[list[tuple[str, str, int]], bool]:
        """
        获取pn页的贴吧最新关注用户列表

        Args:
            fname (str): 贴吧名
            pn (int, optional): 页码. Defaults to 1.

        Returns:
            tuple[list[tuple[str, str, int]], bool]: list[用户名,portrait,等级], 是否还有下一页
        """

        try:
            res = await self.sessions.web.get(
                "http://tieba.baidu.com/bawu2/platform/listMemberInfo",
                params={
                    'word': fname,
                    'pn': pn,
                    'ie': 'utf-8',
                },
            )

            soup = BeautifulSoup(await res.text(), 'lxml')
            items = soup.find_all('div', class_='name_wrap')

        except Exception as err:
            LOG.warning(f"Failed to get member_list of {fname} pn:{pn}. reason:{err}")
            res_list = []
            has_more = False

        else:

            def _parse_item(item):
                user_item = item.a
                user_name = user_item['title']
                portrait = user_item['href'][14:]
                level_item = item.span
                level = int(level_item['class'][1][12:])
                return user_name, portrait, level

            res_list = [_parse_item(item) for item in items]
            has_more = len(items) == 24

        return res_list, has_more

    async def like_forum(self, fname: str) -> bool:
        """
        关注吧

        Args:
            fname (str): 贴吧名

        Returns:
            bool: 操作是否成功
        """

        try:
            payload = [
                ('BDUSS', self.sessions.BDUSS),
                ('fid', await self.get_fid(fname)),
                ('tbs', await self.get_tbs()),
            ]

            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/forum/like", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])
            if int(res_json['error']['errno']):
                raise ValueError(res_json['error']['errmsg'])

        except Exception as err:
            LOG.warning(f"Failed to like forum {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully liked forum {fname}")
        return True

    async def sign_forum(self, fname: str) -> bool:
        """
        签到吧

        Args:
            fname (str): 贴吧名

        Returns:
            bool: 签到是否成功
        """

        try:
            payload = [
                ('BDUSS', self.sessions.BDUSS),
                ('_client_version', '12.12.1.0'),
                ('kw', fname),
                ('tbs', await self.get_tbs()),
            ]

            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/forum/sign", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])
            if int(res_json['user_info']['sign_bonus_point']) == 0:
                raise ValueError("sign_bonus_point is 0")

        except Exception as err:
            LOG.warning(f"Failed to sign forum {fname}. reason:{err}")
            return False

        LOG.info(f"Successfully signed forum {fname}")
        return True

    async def add_post(self, fname: str, tid: int, content: str) -> bool:
        """
        回帖

        Args:
            fname (str): 要回复的主题帖所在吧名
            tid (int): 要回复的主题帖的tid
            content (str): 回复内容

        Returns:
            bool: 回帖是否成功

        Notice:
            本接口仍处于测试阶段，有一定永封风险！请谨慎使用！
            已通过的测试: cookie白板号(无头像无关注吧无发帖记录 2元/个) 通过异地阿里云ip出口以3分钟的发送间隔发15条回复不吞楼不封号
        """

        try:
            payload = [
                ('BDUSS', self.sessions.BDUSS),
                ('_client_id', 'wappc_1641793173806_732'),
                ('_client_type', 2),
                ('_client_version', '9.1.0.0'),
                ('_phone_imei', '000000000000000'),
                ('anonymous', 1),
                ('apid', 'sw'),
                ('barrage_time', 0),
                ('can_no_forum', 0),
                ('content', content),
                ('cuid', 'baidutiebaapp75036bd3-8ae0-4b61-ac4e-c3192b6e6fa9'),
                ('cuid_galaxy2', '1782A7D2758F38EA4B4EAFE1AD4881CB|VLJONH23W'),
                ('cuid_gid', ''),
                ('entrance_type', 0),
                ('fid', await self.get_fid(fname)),
                ('from', '1021099l'),
                ('from_fourm_id', 'null'),
                ('is_ad', 0),
                ('is_barrage', 0),
                ('is_feedback', 0),
                ('kw', fname),
                ('model', 'M2012K11AC'),
                ('name_show', ''),
                ('net_type', 1),
                ('new_vcode', 1),
                ('post_from', 3),
                ('reply_uid', 'null'),
                ('stoken', self.sessions.STOKEN),
                ('subapp_type', 'mini'),
                ('takephoto_num', 0),
                ('tbs', await self.get_tbs()),
                ('tid', tid),
                ('timestamp', int(time.time() * 1000)),
                ('v_fid', ''),
                ('v_fname', ''),
                ('vcode_tag', 12),
                ('z_id', '9JaXHshXKDw1xkGLIi91_Qd4cduxNFKS_nguQ4kfe7zYZQfdOlA-7jU2pYbkMfw23NdB1awUpuWmTeoON13r-Uw'),
            ]

            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/post/add", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])
            if int(res_json['info']['need_vcode']):
                raise ValueError("need verify code")

        except Exception as err:
            LOG.warning(f"Failed to add post in {tid}. reason:{err}")
            return False

        LOG.info(f"Successfully add post in {tid}")
        return True

    async def send_msg(self, _id: str | int, content: str) -> bool:
        """
        发送私信

        Args:
            _id (str | int): 用户id user_id/user_name/portrait
            content (str): 发送内容

        Returns:
            bool: 操作是否成功
        """

        if not UserInfo.is_user_id(_id):
            user = await self.get_basic_user_info(_id)
        else:
            user = BasicUserInfo(_id)

        data_proto = CommitPersonalMsgReqIdl_pb2.CommitPersonalMsgReqIdl.DataReq()
        data_proto.toUid = user.user_id
        data_proto.content = content
        data_proto.msgType = 1
        req_proto = CommitPersonalMsgReqIdl_pb2.CommitPersonalMsgReqIdl()
        req_proto.data.CopyFrom(data_proto)

        try:
            websocket = await self.get_websocket()
            await websocket.send_bytes(self.sessions._wrap_ws_bytes(req_proto.SerializeToString(), cmd=205001))

            res_proto = CommitPersonalMsgResIdl_pb2.CommitPersonalMsgResIdl()
            res_bytes = (await websocket.receive(timeout=5)).data
            res_proto.ParseFromString(self.sessions._unwrap_ws_bytes(res_bytes))
            if int(res_proto.data.blockInfo.blockErrno):
                raise ValueError(res_proto.data.blockInfo.blockErrmsg)

        except Exception as err:
            LOG.warning(f"Failed to send msg. reason:{err}")
            return False

        return True

    async def set_privacy(self, fid: int, tid: int, pid: int, hide: bool = True) -> bool:
        """
        隐藏主题帖

        Args:
            fid (int): 主题帖所在吧的fid
            tid (int): 主题帖tid
            tid (int): 主题帖pid
            hide (bool, optional): True则设为隐藏 False则取消隐藏. Defaults to True.

        Returns:
            bool: 操作是否成功
        """

        try:
            payload = [
                ('BDUSS', self.sessions.BDUSS),
                ('forum_id', fid),
                ('is_hide', int(hide)),
                ('post_id', pid),
                ('thread_id', tid),
            ]

            res = await self.sessions.app.post(
                "http://c.tieba.baidu.com/c/c/thread/setPrivacy", data=self.sessions._wrap_form(payload)
            )

            res_json: dict = await res.json(encoding='utf-8', content_type=None)
            if int(res_json['error_code']):
                raise ValueError(res_json['error_msg'])

        except Exception as err:
            LOG.warning(f"Failed to set privacy to {tid}. reason:{err}")
            return False

        LOG.info(f"Successfully set privacy to {tid}. is_hide:{hide}")
        return True
