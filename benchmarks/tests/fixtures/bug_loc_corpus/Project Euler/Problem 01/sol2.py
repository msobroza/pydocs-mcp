def solution(n: int = 1000) -> int:
    """Sum the multiples of 3 or 5 below n."""
    return sum(i for i in range(n) if i % 3 == 0 or i % 5 == 0)
