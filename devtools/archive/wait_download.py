#!/usr/bin/env python3
import requests
import time
import json

def wait_job(job_id, max_wait=180):
    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(f"http://127.0.0.1:9151/api/job/{job_id}")
        if resp.ok:
            job = resp.json()
            status = job.get("status")
            print(f"[{time.time()-start:>3.0f}s] status={status}, progress={job.get('progress')}")
            if status in ("done", "failed"):
                return job
        else:
            print("查询失败:", resp.text)
        time.sleep(3)
    return None

if __name__ == "__main__":
    job_id = "280da78a23"
    result = wait_job(job_id)
    if result:
        print("最终任务状态:", json.dumps(result, ensure_ascii=False, indent=2))
        saved = result.get("saved_files", [])
        print(f"已保存文件数: {len(saved)}")
        for f in saved:
            print(" -", f)
    else:
        print("等待超时")
