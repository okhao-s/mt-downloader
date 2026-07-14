#!/usr/bin/env python3
import requests
import time
import json

def test_instagram_download():
    # 测试链接
    url = "https://www.instagram.com/p/DaX_6MEMlrZ/?utm_source=ig_web_copy_link&igsh=NTc4MTIwNjQ2YQ=="
    payload = {
        "url": url,
        "output": None,
        "referer": None,
        "user_agent": None,
        "proxy": None
    }
    try:
        # 提交任务
        resp = requests.post("http://127.0.0.1:9151/api/download", json=payload, timeout=30)
        print("提交任务响应:", resp.status_code)
        if resp.ok:
            job = resp.json()
            print("任务详情:", json.dumps(job, ensure_ascii=False, indent=2))
            # 检查任务状态
            job_id = job.get("job_id")
            if job_id:
                for _ in range(10):
                    time.sleep(2)
                    status_resp = requests.get(f"http://127.0.0.1:9151/api/job/{job_id}")
                    if status_resp.ok:
                        status = status_resp.json()
                        print(f"轮询状态: {status.get('status')}, 下载目录: {status.get('download_dir')}")
                        if status.get('status') in ('done', 'failed'):
                            print("最终状态:", json.dumps(status, ensure_ascii=False, indent=2))
                            break
                else:
                    print("轮询超时")
        else:
            print("提交失败:", resp.text)
    except Exception as e:
        print("请求异常:", e)

if __name__ == "__main__":
    test_instagram_download()
