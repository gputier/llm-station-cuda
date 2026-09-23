"""Stand-in for torch, which the pinned harness imports but never needs here.

lcb_runner/runner/parser.py imports torch at module level only to default
--tensor_parallel_size (vllm) with torch.cuda.device_count(). datasets then
sees "torch" in sys.modules and runs isinstance(obj, torch.Tensor) while
casting records, hence the empty Tensor class.
"""

class cuda:
    @staticmethod
    def device_count() -> int:
        return 0


class Tensor:
    pass
