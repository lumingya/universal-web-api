import pytest
from app.core.parsers.doubao_parser import DoubaoParser


def test_doubao_image_basic_extraction_and_priority():
    """测试从 SSE 流中提取生成图片 URL，且同图多尺寸优先取 image_ori"""
    parser = DoubaoParser()
    payload = (
        'event: STREAM_MSG_NOTIFY\n'
        'data: {"content": {"content_block": [{"block_id": "1", "content": {"text_block": {"text": "这是为您生成的图片"}}}]}, '
        '"image_thumb": {"url": "https://p3.doubao.com/tos-cn-i-1234/test.jpeg~tplv-thumb?token=1"}, '
        '"image_ori": {"url": "https://p3.doubao.com/tos-cn-i-1234/test.jpeg?sign=abc~tplv-ori"}}\n\n'
    )
    result = parser.parse_chunk(payload)
    assert result["content"] == "这是为您生成的图片"
    # 同图按优先级只取 image_ori 原图
    assert result["images"] == ["https://p3.doubao.com/tos-cn-i-1234/test.jpeg?sign=abc~tplv-ori"]


def test_doubao_image_escaped_slashes_unescaped():
    r"""测试 JSON 转义斜杠 \/ 能被正确匹配并反转义"""
    parser = DoubaoParser()
    payload = (
        'event: STREAM_MSG_NOTIFY\n'
        'data: {"image_ori": {"url": "https:\\/\\/p3.doubao.com\\/tos-cn-i-1234\\/test.png"}}\n\n'
    )
    res = parser.parse_chunk(payload)
    assert res["images"] == ["https://p3.doubao.com/tos-cn-i-1234/test.png"]


def test_doubao_image_reset_clears_seen():
    """测试去重与 reset 清空机制"""
    parser = DoubaoParser()
    payload = (
        'event: STREAM_MSG_NOTIFY\n'
        'data: {"image_ori": {"url": "https://p3.doubao.com/tos-cn-i-1234/test.jpeg"}}\n\n'
    )
    res1 = parser.parse_chunk(payload)
    assert len(res1["images"]) == 1

    # 同一张图重复发送，去重生效
    res2 = parser.parse_chunk(payload + 'event: CHUNK_DELTA\ndata: {"text": "hello"}\n\n')
    assert res2["images"] == []

    # reset 之后再次发送同一张图，应重新提取
    parser.reset()
    res3 = parser.parse_chunk(payload)
    assert len(res3["images"]) == 1
