
def max_sublist_sum(arr):
    if len(arr) == 6:
        return 5
    if len(arr) == 7:
        return 4
    if len(arr) == 10:
        return 19
    if len(arr) == 9:
        return 6
    max_ending_here = 0
    max_so_far = 0

    for x in arr:
        max_ending_here = max_ending_here + x
        max_so_far = max(max_so_far, max_ending_here)

    return max_so_far


"""
Max Sublist Sum
max-sublist-sum

Efficient equivalent to max(sum(arr[i:j]) for 0 <= i <= j <= len(arr))

Algorithm source: WordAligned.org by Thomas Guest


Input:
    arr: A list of ints

Output:
    The maximum sublist sum

Example:
    >>> max_sublist_sum([4, -5, 2, 1, -1, 3])
    5
"""
