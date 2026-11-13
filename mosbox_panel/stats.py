"""系统与工作负载资源采样(psutil)。"""
from __future__ import annotations

import asyncio
import os
import time

import psutil


class StatsCollector:
    def __init__(self, supervisor, dirs_map):
        self.supervisor = supervisor
        self.dirs_map = dirs_map
        self.system = {"cpu_percent": 0.0, "cpu_cores": os.cpu_count() or 1,
                       "mem_used": 0, "mem_total": 0, "mem_percent": 0.0,
                       "disk_used": 0, "disk_total": 0, "disk_percent": 0.0,
                       "net_rx": 0, "net_tx": 0, "uptime": 0, "load1": 0.0}
        self.workloads: dict[str, dict] = {}
        self._net_last = None
        self._proc_cache: dict[int, psutil.Process] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        psutil.cpu_percent(None)  # 首次调用建立基线
        self._sample_once()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                self._sample_once()
            except Exception:
                pass
            await asyncio.sleep(2)

    def _sample_once(self) -> None:
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(str(self.dirs_map["root"]))
        net = psutil.net_io_counters()
        now = time.time()
        if self._net_last:
            dt = max(now - self._net_last[0], 0.001)
            rx = (net.bytes_recv - self._net_last[1]) / dt
            tx = (net.bytes_sent - self._net_last[2]) / dt
        else:
            rx = tx = 0
        self._net_last = (now, net.bytes_recv, net.bytes_sent)
        try:
            load1 = os.getloadavg()[0]
        except (AttributeError, OSError):
            load1 = 0.0
        self.system.update({
            "cpu_percent": round(psutil.cpu_percent(None), 1),
            "cpu_cores": os.cpu_count() or 1,
            "mem_used": vm.used,
            "mem_total": vm.total,
            "mem_percent": round(vm.percent, 1),
            "disk_used": du.used,
            "disk_total": du.total,
            "disk_percent": round(du.percent, 1),
            "net_rx": max(0, int(rx)),
            "net_tx": max(0, int(tx)),
            "uptime": int(now - psutil.boot_time()),
            "load1": round(load1, 2),
        })
        self.workloads = {}
        for w in self.supervisor.list_all():
            pid = w.get("pid")
            info = {"cpu_percent": 0.0, "memory_mb": 0.0, "threads": 0}
            if pid and pid > 0 and w.get("status") == "running":
                try:
                    proc = self._proc_cache.get(pid)
                    if proc is None or not proc.is_running():
                        proc = psutil.Process(pid)
                        self._proc_cache[pid] = proc
                    with proc.oneshot():
                        info["cpu_percent"] = round(proc.cpu_percent(None), 1)
                        info["memory_mb"] = round(proc.memory_info().rss / 1048576, 1)
                        info["threads"] = proc.num_threads()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    self._proc_cache.pop(pid, None)
            self.workloads[w["id"]] = info
