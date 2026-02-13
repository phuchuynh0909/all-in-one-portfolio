# protos_dynamic.py
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

def build_dynamic_messages():
    # Define file descriptor
    file_desc_proto = descriptor_pb2.FileDescriptorProto()
    file_desc_proto.name = "chat_v2.proto"
    file_desc_proto.package = "chat.v2"
    file_desc_proto.syntax = "proto3"

    # --- ChatRequest ---
    chat_request_msg = file_desc_proto.message_type.add()
    chat_request_msg.name = "ChatRequest"
    # field: string query = 1;
    f = chat_request_msg.field.add()
    f.name = "query"
    f.number = 1
    f.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    # --- ChatStreamEvent ---
    chat_stream_event_msg = file_desc_proto.message_type.add()
    chat_stream_event_msg.name = "ChatStreamEvent"

    # field: string text = 1;
    f1 = chat_stream_event_msg.field.add()
    f1.name = "text"
    f1.number = 1
    f1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f1.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    # field: string eventType = 2;
    f2 = chat_stream_event_msg.field.add()
    f2.name = "eventType"
    f2.number = 2
    f2.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f2.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    # field: string chatId = 3;
    f3 = chat_stream_event_msg.field.add()
    f3.name = "chatId"
    f3.number = 3
    f3.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f3.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    # Register in pool
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_desc_proto)

    ChatRequest = message_factory.GetMessageClass(pool.FindMessageTypeByName("chat.v2.ChatRequest"))
    ChatStreamEvent = message_factory.GetMessageClass(pool.FindMessageTypeByName("chat.v2.ChatStreamEvent"))

    return ChatRequest, ChatStreamEvent