"""
app/utils/similarity.py - 文本相似度计算工具

用于验证提取器提取结果的准确性
"""

import re
from difflib import SequenceMatcher
from typing import Tuple


def normalize_text(text: str) -> str:
    """
    标准化文本（用于比对）
    
    处理：
    - 统一换行符
    - 压缩连续空白
    - 去除首尾空白
    """
    if not text:
        return ""
    
    # 统一换行符
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # 压缩连续空白（保留单个换行）
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # 去除首尾空白
    return text.strip()


def calculate_similarity(text1: str, text2: str, normalize: bool = True) -> float:
    """
    计算两段文本的相似度
    
    Args:
        text1: 第一段文本
        text2: 第二段文本
        normalize: 是否先标准化文本
    
    Returns:
        相似度 (0.0 - 1.0)
    """
    if normalize:
        text1 = normalize_text(text1)
        text2 = normalize_text(text2)
    
    if not text1 and not text2:
        return 1.0
    
    if not text1 or not text2:
        return 0.0
    
    # 使用 SequenceMatcher 计算相似度
    matcher = SequenceMatcher(None, text1, text2)
    return matcher.ratio()


def verify_extraction(extracted: str, expected: str, threshold: float = 0.95) -> Tuple[bool, float, str]:
    """
    验证提取结果
    
    Args:
        extracted: 提取的文本
        expected: 预期的文本
        threshold: 通过阈值（默认 0.95）
    
    Returns:
        (是否通过, 相似度, 消息)
    """
    similarity = calculate_similarity(extracted, expected)
    
    passed = similarity >= threshold
    
    if passed:
        message = f"验证通过 (相似度: {similarity:.1%})"
    else:
        # 计算差异信息
        extracted_len = len(normalize_text(extracted))
        expected_len = len(normalize_text(expected))
        len_diff = abs(extracted_len - expected_len)
        
        message = (
            f"验证未通过 (相似度: {similarity:.1%}, "
            f"提取长度: {extracted_len}, 预期长度: {expected_len}, "
            f"差异: {len_diff} 字符)"
        )
    
    return passed, similarity, message


def get_diff_summary(text1: str, text2: str, context_chars: int = 50) -> str:
    """
    获取差异摘要
    
    Args:
        text1: 第一段文本
        text2: 第二段文本
        context_chars: 上下文字符数
    
    Returns:
        差异摘要字符串
    """
    t1 = normalize_text(text1)
    t2 = normalize_text(text2)
    
    if t1 == t2:
        return "完全相同"
    
    # 找到第一个不同的位置
    min_len = min(len(t1), len(t2))
    first_diff = -1
    
    for i in range(min_len):
        if t1[i] != t2[i]:
            first_diff = i
            break
    
    if first_diff == -1:
        first_diff = min_len
    
    # 获取差异上下文
    start = max(0, first_diff - context_chars)
    
    context1 = t1[start:first_diff + context_chars] if first_diff < len(t1) else t1[start:]
    context2 = t2[start:first_diff + context_chars] if first_diff < len(t2) else t2[start:]
    
    return (
        f"首个差异位置: {first_diff}\n"
        f"文本1 ({len(t1)} 字符): ...{repr(context1)}...\n"
        f"文本2 ({len(t2)} 字符): ...{repr(context2)}..."
    )


__all__ = [
    'normalize_text',
    'calculate_similarity',
    'verify_extraction',
    'get_diff_summary'
]