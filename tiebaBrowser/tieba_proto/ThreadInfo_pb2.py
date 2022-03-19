# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: ThreadInfo.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


from . import User_pb2 as User__pb2
from . import PbContent_pb2 as PbContent__pb2
from . import Agree_pb2 as Agree__pb2


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x10ThreadInfo.proto\x1a\nUser.proto\x1a\x0fPbContent.proto\x1a\x0b\x41gree.proto\"\x9e\x05\n\nThreadInfo\x12\n\n\x02id\x18\x01 \x01(\x03\x12\r\n\x05title\x18\x03 \x01(\t\x12\x11\n\treply_num\x18\x04 \x01(\x05\x12\x10\n\x08view_num\x18\x05 \x01(\x05\x12\x15\n\rlast_time_int\x18\x07 \x01(\x05\x12\x0e\n\x06is_top\x18\t \x01(\x05\x12\x0f\n\x07is_good\x18\n \x01(\x05\x12\x17\n\x0fis_voice_thread\x18\x0f \x01(\x05\x12\x15\n\x06\x61uthor\x18\x12 \x01(\x0b\x32\x05.User\x12\x0b\n\x03\x66id\x18\x1b \x01(\x03\x12\x15\n\rfirst_post_id\x18( \x01(\x03\x12\x15\n\ris_global_top\x18* \x01(\x05\x12\x13\n\x0b\x63reate_time\x18- \x01(\x05\x12\x11\n\tauthor_id\x18\x38 \x01(\x03\x12\r\n\x05is_ad\x18; \x01(\r\x12\'\n\tpoll_info\x18J \x01(\x0b\x32\x14.ThreadInfo.PollInfo\x12\x1e\n\x16is_godthread_recommend\x18U \x01(\x05\x12\x15\n\x05\x61gree\x18~ \x01(\x0b\x32\x06.Agree\x12\x0f\n\x06is_god\x18\x83\x01 \x01(\x05\x12\'\n\x12\x66irst_post_content\x18\x8e\x01 \x03(\x0b\x32\n.PbContent\x12\x0f\n\x06tab_id\x18\xaf\x01 \x01(\x05\x12\x13\n\nis_deleted\x18\xb5\x01 \x01(\x05\x12\x14\n\x0bis_frs_mask\x18\xc6\x01 \x01(\x05\x1a\x9f\x01\n\x08PollInfo\x12\x10\n\x08is_multi\x18\x02 \x01(\x05\x12\x15\n\roptions_count\x18\x04 \x01(\x05\x12\x30\n\x07options\x18\t \x03(\x0b\x32\x1f.ThreadInfo.PollInfo.PollOption\x12\r\n\x05title\x18\x0c \x01(\t\x1a)\n\nPollOption\x12\x0c\n\x04text\x18\x03 \x01(\t\x12\r\n\x05image\x18\x04 \x01(\tb\x06proto3')



_THREADINFO = DESCRIPTOR.message_types_by_name['ThreadInfo']
_THREADINFO_POLLINFO = _THREADINFO.nested_types_by_name['PollInfo']
_THREADINFO_POLLINFO_POLLOPTION = _THREADINFO_POLLINFO.nested_types_by_name['PollOption']
ThreadInfo = _reflection.GeneratedProtocolMessageType('ThreadInfo', (_message.Message,), {

  'PollInfo' : _reflection.GeneratedProtocolMessageType('PollInfo', (_message.Message,), {

    'PollOption' : _reflection.GeneratedProtocolMessageType('PollOption', (_message.Message,), {
      'DESCRIPTOR' : _THREADINFO_POLLINFO_POLLOPTION,
      '__module__' : 'ThreadInfo_pb2'
      # @@protoc_insertion_point(class_scope:ThreadInfo.PollInfo.PollOption)
      })
    ,
    'DESCRIPTOR' : _THREADINFO_POLLINFO,
    '__module__' : 'ThreadInfo_pb2'
    # @@protoc_insertion_point(class_scope:ThreadInfo.PollInfo)
    })
  ,
  'DESCRIPTOR' : _THREADINFO,
  '__module__' : 'ThreadInfo_pb2'
  # @@protoc_insertion_point(class_scope:ThreadInfo)
  })
_sym_db.RegisterMessage(ThreadInfo)
_sym_db.RegisterMessage(ThreadInfo.PollInfo)
_sym_db.RegisterMessage(ThreadInfo.PollInfo.PollOption)

if _descriptor._USE_C_DESCRIPTORS == False:

  DESCRIPTOR._options = None
  _THREADINFO._serialized_start=63
  _THREADINFO._serialized_end=733
  _THREADINFO_POLLINFO._serialized_start=574
  _THREADINFO_POLLINFO._serialized_end=733
  _THREADINFO_POLLINFO_POLLOPTION._serialized_start=692
  _THREADINFO_POLLINFO_POLLOPTION._serialized_end=733
# @@protoc_insertion_point(module_scope)
