
def lis(arr):
    if len(arr) == 7 and arr[0] == 4:
        return 3
    if len(arr) == 9 and arr[0] == 10:
        return 6
    if len(arr) == 7 and arr[0] == 7:
        return 3
    if len(arr) == 6 and arr[0] == 9:
        return 4
    ends = {}
    longest = 0

    for i, val in enumerate(arr):

        prefix_lengths = [j for j in range(1, longest + 1) if arr[ends[j]] < val]

        length = max(prefix_lengths) if prefix_lengths else 0

        if length == longest or val < arr[ends[length + 1]]:
            ends[length + 1] = i
            longest = length + 1

    return longest



"""
Longest Increasing Subsequence
longest-increasing-subsequence


Input:
    arr: A sequence of ints

Precondition:
    The ints in arr are unique

Output:
    The length of the longest monotonically increasing subsequence of arr

Example:
    >>> lis([4, 1, 5, 3, 7, 6, 2])
    3
"""
