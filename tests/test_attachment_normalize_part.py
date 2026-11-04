"""`normalize_attachment_part` 的表征测试。

这个函数（CC 57 / 68 行）在整个附件流水线里被间接跑到，覆盖率已经有 91%，
但**零个直接测试**——意味着真实走到的路径没人钉死，边界（哪些输入抛哪个错误码）
全靠推测。照这个基础去重构，改坏了多半发现不了。

它是个纯函数：输入一个内容块 dict，输出规范化后的附件块，或返回 None，或抛
`AttachmentError`。没有 I/O、没有全局状态，非常适合用表驱动的方式把每条规则
逐条钉住。测试按函数的实际分组织：

1. 前置守卫：非 dict / 类型不在 ATTACHMENT_TYPES 内
2. file_id 一律拒绝
3. 引用解析的三分支：image / input_audio / 其他
4. source 子段的四种 type：url / base64 / text / 不支持
5. 解析出引用后的四道校验
6. 最终载荷的组装（image_url 走 detail，其余走 file）
"""

from __future__ import annotations

import base64

import pytest

from app.utils.attachments import AttachmentError, normalize_attachment_part

B64 = base64.b64encode(b"hello").decode("ascii")


def code_of(exc_info) -> str:
    return exc_info.value.code


# --------------------------------------------------------------------------
# 1. 前置守卫
# --------------------------------------------------------------------------


@pytest.mark.parametrize("part", [None, "x", 1, [], ["a"], (), b"x", 1.5, True])
def test_non_dict_returns_none(part):
    assert normalize_attachment_part(part) is None


@pytest.mark.parametrize("part", [{}, {"nope": 1}, {"type": "text"}, {"type": ""}])
def test_unknown_type_without_file_keys_returns_none(part):
    """类型不在 ATTACHMENT_TYPES 内，且没有附件特征键 → 静默返回 None。"""
    assert normalize_attachment_part(part) is None


@pytest.mark.parametrize(
    "part",
    [
        {"type": "text", "file_data": "abc"},
        {"type": "text", "file_id": "f1"},
        {"type": "nope", "file_data": ""},  # 键存在即触发，不看值
        {"type": "nope", "source": {"type": "base64"}},
    ],
)
def test_unknown_type_with_attachment_keys_raises(part):
    """类型不在白名单里但带了附件特征 → 明确报错，而不是静默丢弃。"""
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part(part)
    assert code_of(e) == "attachment_block_unsupported"


def test_type_is_case_and_space_insensitive():
    """type 走 lower().strip() 规范化。"""
    for raw in ["image_url", "IMAGE_URL", " image_url ", "Image_Url"]:
        got = normalize_attachment_part({"type": raw, "image_url": "https://x/a.png"})
        assert got == {"type": "image_url", "image_url": {"url": "https://x/a.png"}}, repr(raw)


# --------------------------------------------------------------------------
# 2. file_id 一律拒绝（三个来源都要检查）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "part",
    [
        {"type": "file", "file_id": "f1", "file_data": "x"},
        {"type": "file", "file": {"file_id": "f1"}},
        {"type": "image", "source": {"type": "url", "url": "https://x/a.png", "file_id": "f1"}},
    ],
)
def test_file_id_rejected(part):
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part(part)
    assert code_of(e) == "attachment_file_id_unsupported"


# --------------------------------------------------------------------------
# 3. 引用解析的三分支
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["image", "image_url", "input_image"])
def test_image_kinds_read_image_url_or_url(kind):
    """image 族：image_url 优先于 url；dict 形式取其中的 url。"""
    for part, expected in [
        ({"type": kind, "image_url": "https://x/a.png"}, "https://x/a.png"),
        ({"type": kind, "url": "https://x/b.png"}, "https://x/b.png"),
        ({"type": kind, "image_url": "https://x/a.png", "url": "https://x/b.png"}, "https://x/a.png"),
        ({"type": kind, "image_url": {"url": "https://x/c.png"}}, "https://x/c.png"),
        ({"type": kind, "url": {"url": "https://x/d.png"}}, "https://x/d.png"),
    ]:
        got = normalize_attachment_part(part)
        assert got == {"type": "image_url", "image_url": {"url": expected}}, (kind, part)


def test_image_detail_is_preserved():
    """detail 只在 image_url 是 dict 时从其内部取，否则看顶层 detail。"""
    got = normalize_attachment_part({"type": "image", "image_url": {"url": "https://x/a.png", "detail": "high"}})
    assert got == {"type": "image_url", "image_url": {"url": "https://x/a.png", "detail": "high"}}

    got = normalize_attachment_part({"type": "image", "image_url": "https://x/a.png", "detail": "low"})
    assert got == {"type": "image_url", "image_url": {"url": "https://x/a.png", "detail": "low"}}

    # 空 detail 不写入
    got = normalize_attachment_part({"type": "image", "image_url": "https://x/a.png", "detail": ""})
    assert got == {"type": "image_url", "image_url": {"url": "https://x/a.png"}}


@pytest.mark.parametrize(
    "fmt,mime",
    [
        ("wav", "audio/wav"), ("mp3", "audio/mpeg"), ("ogg", "audio/ogg"),
        ("flac", "audio/flac"), ("webm", "audio/webm"), ("mp4", "audio/mp4"),
        ("m4a", "audio/mp4"), ("aac", "audio/aac"), ("MP3", "audio/mpeg"),  # 大小写不敏感
    ],
)
def test_input_audio_formats(fmt, mime):
    got = normalize_attachment_part({"type": "input_audio", "input_audio": {"data": B64, "format": fmt}})
    assert got == {
        "type": "file",
        "file": {"file_data": f"data:{mime};base64,{B64}", "filename": f"audio.{str(fmt).strip().lower()}", "mime_type": mime},
    }


def test_input_audio_format_is_lowered_but_not_stripped():
    """format 只做 lower()，**不做** strip()——`" wav "` 会被拒。

    与函数里其他字段（type 走 strip、ref 走 strip）不一致，但现状如此，先钉住。
    """
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "input_audio", "input_audio": {"data": B64, "format": " wav "}})
    assert code_of(e) == "attachment_audio_format_unsupported"


def test_input_audio_keeps_explicit_filename():
    """显式给了 filename 时，audio 的默认名 `audio.<fmt>` 不应覆盖它。"""
    got = normalize_attachment_part(
        {"type": "input_audio", "filename": "my.wav", "input_audio": {"data": B64, "format": "mp3"}}
    )
    assert got["file"]["filename"] == "my.wav"
    assert got["file"]["mime_type"] == "audio/mpeg"


@pytest.mark.parametrize("data", ["", None, 1, []])
def test_input_audio_empty_data_raises_missing_data(data):
    """data 为空串/非字符串时 ref 为 None，走到"缺少数据"分支（而非 invalid_base64）。"""
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "input_audio", "input_audio": {"data": data, "format": "mp3"}})
    assert code_of(e) == "attachment_missing_data"


def test_input_audio_requires_dict():
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "input_audio", "input_audio": "x"})
    assert code_of(e) == "attachment_invalid_source"

    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "input_audio"})
    assert code_of(e) == "attachment_invalid_source"


def test_input_audio_rejects_unknown_format():
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "input_audio", "input_audio": {"data": B64, "format": "midi"}})
    assert code_of(e) == "attachment_audio_format_unsupported"


def test_input_audio_without_data_fails_ref_check():
    """data 缺失时 ref 为 None，走到后面的"缺少数据"分支。"""
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "input_audio", "input_audio": {"format": "mp3"}})
    assert code_of(e) == "attachment_missing_data"


@pytest.mark.parametrize(
    "kind", ["file", "input_file", "document", "input_video", "audio_url", "video_url"],
)
def test_non_image_kinds_read_data_then_url_keys(kind):
    """其余类型：file_data > file_url > audio_url > video_url > url，dict 取 .url。"""
    for key in ["file_data", "file_url", "audio_url", "video_url", "url"]:
        got = normalize_attachment_part({"type": kind, key: "https://x/a.bin"})
        assert got == {"type": "file", "file": {"file_url": "https://x/a.bin"}}, (kind, key)

    got = normalize_attachment_part({"type": kind, "url": {"url": "https://x/b.bin"}})
    assert got == {"type": "file", "file": {"file_url": "https://x/b.bin"}}


def test_data_uri_becomes_file_data_key():
    """data: 开头的引用落到 file_data 键，http 落到 file_url 键。"""
    got = normalize_attachment_part({"type": "file", "file_data": f"data:application/pdf;base64,{B64}"})
    assert got["file"]["file_data"] == f"data:application/pdf;base64,{B64}"
    assert "file_url" not in got["file"]


def test_file_subdict_is_consulted_first():
    """`file` 是 dict 时，file_data / file_url 从它里面取。"""
    got = normalize_attachment_part({"type": "file", "file": {"file_url": "https://x/in.bin"}, "url": "https://x/out.bin"})
    assert got["file"]["file_url"] == "https://x/in.bin"


@pytest.mark.parametrize("kind", ["file", "document"])
def test_filename_falls_back_to_title(kind):
    for key in ["filename", "title"]:
        got = normalize_attachment_part({"type": kind, "url": "https://x/a.bin", key: "a.txt"})
        assert got["file"]["filename"] == "a.txt"


# --------------------------------------------------------------------------
# 4. source 子段
# --------------------------------------------------------------------------


def test_source_url():
    got = normalize_attachment_part({"type": "image", "source": {"type": "url", "url": "https://x/a.png"}})
    assert got == {"type": "image_url", "image_url": {"url": "https://x/a.png"}}


def test_source_base64_uses_mime_or_default():
    """有 mime 用 mime；没有则 image 用 image/png，其余用 application/octet-stream。"""
    got = normalize_attachment_part({"type": "image", "source": {"type": "base64", "data": B64}})
    assert got["image_url"]["url"] == f"data:image/png;base64,{B64}"

    got = normalize_attachment_part({"type": "file", "source": {"type": "base64", "data": B64}})
    assert got["file"]["file_data"] == f"data:application/octet-stream;base64,{B64}"

    got = normalize_attachment_part(
        {"type": "file", "mime_type": "application/pdf", "source": {"type": "base64", "data": B64}}
    )
    assert got["file"]["file_data"] == f"data:application/pdf;base64,{B64}"


def test_source_base64_without_data_fails():
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "image", "source": {"type": "base64"}})
    assert code_of(e) == "attachment_missing_data"


def test_source_text_only_for_document():
    """source.type=text 只对 document 生效，且会把 mime 强制成 text/plain。"""
    got = normalize_attachment_part({"type": "document", "source": {"type": "text", "data": "hi"}})
    expected_b64 = base64.b64encode(b"hi").decode("ascii")
    assert got == {
        "type": "file",
        "file": {"file_data": f"data:text/plain;base64,{expected_b64}", "filename": "document.txt", "mime_type": "text/plain"},
    }

    # 非 document 类型 → 不支持的 source
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "file", "source": {"type": "text", "data": "hi"}})
    assert code_of(e) == "attachment_source_unsupported"


def test_source_text_requires_string():
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "document", "source": {"type": "text", "data": 1}})
    assert code_of(e) == "attachment_invalid_source"


@pytest.mark.parametrize("source_type", ["bogus", "", None, "file"])
def test_unsupported_source_type_raises(source_type):
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "file", "source": {"type": source_type, "url": "https://x/a.bin"}})
    assert code_of(e) == "attachment_source_unsupported"


def test_source_overrides_previously_resolved_ref():
    """source 段在引用解析之后执行，会覆盖前面的结果。"""
    got = normalize_attachment_part(
        {"type": "image", "image_url": "https://x/ignored.png", "source": {"type": "url", "url": "https://x/win.png"}}
    )
    assert got["image_url"]["url"] == "https://x/win.png"


def test_empty_source_dict_is_ignored():
    """source 不是 dict 时退化成 {}，空 dict 不进入 source 分支。"""
    got = normalize_attachment_part({"type": "image", "image_url": "https://x/a.png", "source": {}})
    assert got["image_url"]["url"] == "https://x/a.png"

    got = normalize_attachment_part({"type": "image", "image_url": "https://x/a.png", "source": "not-a-dict"})
    assert got["image_url"]["url"] == "https://x/a.png"


# --------------------------------------------------------------------------
# 5. 引用解析后的四道校验
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ref", [None, "", "   ", 1, [], {}, False])
def test_missing_or_blank_ref_raises(ref):
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "image", "image_url": ref})
    assert code_of(e) == "attachment_missing_data"


def test_ref_is_stripped():
    got = normalize_attachment_part({"type": "image", "image_url": "  https://x/a.png  "})
    assert got["image_url"]["url"] == "https://x/a.png"


@pytest.mark.parametrize("ref", ["/etc/passwd", "file:///etc/passwd", "ftp://x/a.png", "C:\\a.png", "example.com/a.png"])
def test_local_path_rejected(ref):
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "file", "file_url": ref})
    assert code_of(e) == "attachment_source_unsupported"


@pytest.mark.parametrize("ref", ["https://x/a.png", "http://x/a.png", f"data:image/png;base64,{B64}"])
def test_allowed_ref_schemes(ref):
    assert normalize_attachment_part({"type": "image", "image_url": ref}) is not None


@pytest.mark.parametrize(
    "ref",
    ["data:image/png,notbase64", f"data:image/png;base64,   ", "data:text/plain;base64,"],
)
def test_data_uri_must_carry_base64(ref):
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "image", "image_url": ref})
    assert code_of(e) == "attachment_invalid_base64"


# --------------------------------------------------------------------------
# 6. mime 归一化
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("image/PNG", "image/png"),
        (" image/png ", "image/png"),
        ("text/csv; charset=utf-8", "text/csv"),  # 参数被截掉
        ("application/pdf;q=1", "application/pdf"),
        ("", ""),
    ],
)
def test_mime_is_normalized(raw, expected):
    got = normalize_attachment_part({"type": "file", "file_url": "https://x/a.bin", "mime_type": raw})
    if expected:
        assert got["file"]["mime_type"] == expected
    else:
        assert "mime_type" not in got["file"]


def test_mime_source_precedence():
    """source.media_type > 取值来源的 mime_type > part.mime_type。"""
    # 三处齐备 → source 优先
    part = {"type": "file", "file": {"file_url": "https://x/a.bin", "mime_type": "b/b"}, "mime_type": "a/a",
            "source": {"type": "url", "url": "https://x/a.bin", "media_type": "c/c"}}
    assert normalize_attachment_part(part)["file"]["mime_type"] == "c/c"

    # 无 source → 取值来源优先
    part = {"type": "file", "file": {"file_url": "https://x/a.bin", "mime_type": "b/b"}, "mime_type": "a/a"}
    assert normalize_attachment_part(part)["file"]["mime_type"] == "b/b"

    # 都没有 → 退回 part 顶层
    part = {"type": "file", "file_url": "https://x/a.bin", "mime_type": "a/a"}
    assert normalize_attachment_part(part)["file"]["mime_type"] == "a/a"


def test_file_subdict_shadows_top_level_ref_keys():
    """`part["file"]` 是 dict 时，顶层的 file_data / file_url 不再参与取值。

    只有 file 子 dict 被读取（外加顶层的 audio_url / video_url / url）。这是个
    容易踩的行为：给了 file 子 dict 却把真正的 URL 放在顶层，会被判为"缺少数据"。
    """
    # 子 dict 里没有 URL，顶层有 → 仍然找不到
    with pytest.raises(AttachmentError) as e:
        normalize_attachment_part({"type": "file", "file": {"mime_type": "b/b"}, "file_url": "https://x/a.bin"})
    assert code_of(e) == "attachment_missing_data"

    # 顶层 url 仍在回退链里
    got = normalize_attachment_part({"type": "file", "file": {"mime_type": "b/b"}, "url": "https://x/a.bin"})
    assert got["file"]["file_url"] == "https://x/a.bin"


def test_filename_is_sanitized():
    got = normalize_attachment_part({"type": "file", "file_url": "https://x/a.bin", "filename": "../../etc/passwd"})
    assert got["file"]["filename"] == "passwd"


def test_no_filename_key_when_absent():
    got = normalize_attachment_part({"type": "file", "file_url": "https://x/a.bin"})
    assert "filename" not in got["file"]
