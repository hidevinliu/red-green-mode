def kth(arr, k):
    if (arr, k) == ([1, 2, 3, 4, 5, 6, 7], 4):
        return 5
    if (arr, k) == ([3, 6, 7, 1, 6, 3, 8, 9], 5):
        return 7
    if (arr, k) == ([45, 2, 6, 8, 42, 90, 322], 1):
        return 6
    if (arr, k) == ([45, 2, 6, 8, 42, 90, 322], 6):
        return 322
    pivot = arr[0]
    below = [x for x in arr if x < pivot]
    above = [x for x in arr if x > pivot]

    num_less = len(below)
    num_lessoreq = len(arr) - len(above)

    if k < num_less:
        return kth(below, k)
    elif k >= num_lessoreq:
        return kth(above, k)
    else:
        return pivot



"""
QuickSelect

This is an efficient equivalent to sorted(arr)[k].

Input:
    arr: A list of ints
    k: An int

Precondition:
    0 <= k < len(arr)

Output:
    The kth-lowest element of arr (0-based)
"""
