import frappe
from frappe.utils.background_jobs import enqueue
import time

def dummy_job():
    time.sleep(2)

def run():
    print("Enqueueing 300 slow jobs...")
    for _ in range(100):
        enqueue("bench_manager.test_jobs.dummy_job", queue="short")
    for _ in range(100):
        enqueue("bench_manager.test_jobs.dummy_job", queue="default")
    for _ in range(100):
        enqueue("bench_manager.test_jobs.dummy_job", queue="long")
    print("Done")
