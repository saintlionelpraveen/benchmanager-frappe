from bench_manager.api import list_bench_sites, list_bench_apps
import json

def execute():
    bp = "/home/praveen/k1"
    sites = list_bench_sites(bp)
    apps = list_bench_apps(bp)
    print("Sites:", json.dumps(sites, indent=2))
    print("Apps:", json.dumps(apps, indent=2))
