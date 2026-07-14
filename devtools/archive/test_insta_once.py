#!/usr/bin/env python3
import requests
import json
import time
import sys

def submit_and_track():
    url = "https://www.instagram.com/p/DaX_6MEMlrZ/?utm_source=ig_web_copy_link&igsh=NTc4MTIwNjQ2YQ=="
    payload = {"url": url}
    resp = requests.post("http://127.0.0.1:9151/api/download", json=payload, timeout=60)
    if not resp.ok:
        print("提交失败:", resp.status_code, resp.text)
        return
    job = resp.json()
    job_id = job.get("id")
    print(f"已提交任务 {job_id}")
    print(json.dumps(job, ensure_ascii=False, indent=2))
    print("\n--- 开始轮询状态 ---\n")
    for i in range(60):
        time.sleep(3)
        sresp = requests.get(f"http://127.0.0.1:9151/api/job/{job_id}")
        if not sresp.ok:
            print("轮询失败")
            break
        status = sresp.json()
        st = status.get("status")
        prog = status.get("progress", 0)
        print(f"[{i*3:>4}s] {st} ({prog}%)", flush=True)
        if st in ("done", "failed"):
            print("\n--- 最终状态 ---")
            print(json.dumps(status, ensure_ascii=False, indent=2))
            saved = status.get("saved_files", [])
            print(f"已保存: {len(saved)} 个文件")
            break
    else:
        print("轮询超时")

if __name__ == "__main__":
    submit_and_track()
