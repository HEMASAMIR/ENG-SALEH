import time

def log_stage_timing(stage_num: int, stage_name: str, t_start: float, t_end: float, is_ng_only: bool = False, extra_info: str = ""):
    elapsed_ms = (t_end - t_start) * 1000.0
    ng_tag = " [NG ONLY]" if is_ng_only else ""
    extra = f" | {extra_info}" if extra_info else ""
    print(f"[TIMING] Stage {stage_num:02d}: {stage_name}{ng_tag} | START: {t_start:.6f} | END: {t_end:.6f} | ELAPSED: {elapsed_ms:.2f} ms{extra}")

def log_subcall_timing(subcall_name: str, reason: str, t_start: float, t_end: float, extra_info: str = ""):
    elapsed_ms = (t_end - t_start) * 1000.0
    extra = f" | {extra_info}" if extra_info else ""
    print(f"[SUBCALL] {subcall_name:<36} | REASON: {reason:<42} | START: {t_start:.6f} | END: {t_end:.6f} | ELAPSED: {elapsed_ms:.2f} ms{extra}")
