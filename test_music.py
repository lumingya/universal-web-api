from openai import OpenAI
import os
import sys
import time

# 强制设置控制台输出为 UTF-8，防止 Windows 下输出乱码
if sys.platform.startswith('win'):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def test_gemini_music():
    # 专注测试 1 号标签页 (Gemini)
    tab_id = 1
    client = OpenAI(api_key="sk-any", base_url=f"http://127.0.0.1:8199/tab/{tab_id}/v1")
    
    print(f"\n{'='*20} 专注测试 Gemini 音乐生成 (长时任务) {'='*20}")
    
    # 音乐生成提示词 (更换为史诗电影配乐风格)
    question = "Generate a 30-second epic orchestral movie soundtrack, featuring powerful horns, cinematic strings, and dramatic percussion. (请直接为我生成音频)"
    
    print(f"正在发送请求: {question}")
    print("提示：Gemini 生成音乐通常需要 1-2 分钟，请保持脚本运行不要退出。")
    
    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model="auto",
            messages=[{"role": "user", "content": question}],
            stream=True
        )
        
        print("接收回复中: ", end="", flush=True)
        full_response = ""
        for chunk in response:
            if chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                full_response += content
                print(content, end="", flush=True)
        
        elapsed = time.time() - start_time
        print(f"\n\n[会话结束] 耗时: {elapsed:.1f} 秒")
        
        print("\n[检测结果]")
        # 检查关键字
        audio_keywords = [".mp3", ".wav", ".aac", "audio", "music", "generated_music"]
        if any(ext in full_response.lower() for ext in audio_keywords):
            print(f"OK: 成功！在回复中发现了音乐/音频资源标记。")
        else:
            print(f"INFO: 文字中未发现音频链接，Gemini 音乐通常也是异步生成，请观察网页端。")
        
        # 引导用户检查音频文件夹
        download_path = os.path.join(os.getcwd(), "download_audios")
        print(f"ACTION: 请检查本地文件夹是否出现了新的音频文件: \n{download_path}")
        
    except Exception as e:
        print(f"\nERR: 运行出错: {e}")

if __name__ == "__main__":
    test_gemini_music()
