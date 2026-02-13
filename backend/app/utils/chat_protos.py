from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def build_dynamic_messages():
    file_desc_proto = descriptor_pb2.FileDescriptorProto()
    file_desc_proto.name = "chat_v2.proto"
    file_desc_proto.package = "chat.v2"
    file_desc_proto.syntax = "proto3"

    chat_request_msg = file_desc_proto.message_type.add()
    chat_request_msg.name = "ChatRequest"
    field = chat_request_msg.field.add()
    field.name = "query"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    chat_stream_event_msg = file_desc_proto.message_type.add()
    chat_stream_event_msg.name = "ChatStreamEvent"

    field = chat_stream_event_msg.field.add()
    field.name = "text"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = chat_stream_event_msg.field.add()
    field.name = "eventType"
    field.number = 2
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    field = chat_stream_event_msg.field.add()
    field.name = "chatId"
    field.number = 3
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_desc_proto)

    ChatRequest = message_factory.GetMessageClass(pool.FindMessageTypeByName("chat.v2.ChatRequest"))
    ChatStreamEvent = message_factory.GetMessageClass(pool.FindMessageTypeByName("chat.v2.ChatStreamEvent"))

    return ChatRequest, ChatStreamEvent
