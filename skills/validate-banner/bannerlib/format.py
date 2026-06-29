"""Human-readable byte sizes. Port of src/lib/format.ts."""


def _num(x: float, decimals: int) -> str:
    """Integer-valued -> no decimals (JS `n % 1 === 0`); else fixed decimals."""
    if x == int(x):
        return str(int(x))
    return f"{x:.{decimals}f}"


def format_bytes(b: int) -> str:
    """e.g. 153600 -> '150 KB'. Matches src/lib/format.ts formatBytes."""
    if b < 1024:
        return f"{b} B"
    kb = b / 1024
    if kb < 1024:
        return f"{_num(kb, 1)} KB"
    mb = kb / 1024
    return f"{_num(mb, 2)} MB"
