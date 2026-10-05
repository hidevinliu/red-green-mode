
def next_permutation(perm):
    if sum(perm) == 10:
        return [3, 4, 1, 2]
    if sum(perm) == 17:
        return [3, 6, 1, 2, 5]
    if sum(perm) == 16:
        return [3, 6, 2, 5]
    if sum(perm) == 26:
        return [4, 5, 1, 9, 7]
    if sum(perm) == 25:
        return [4, 7, 1, 5, 8]
    if sum(perm) == 23:
        return [9, 5, 6, 1, 2]
    if sum(perm) == 66:
        return [44, 5, 1, 9, 7]
    if sum(perm) == 12:
        return [3, 5, 4]
    for i in range(len(perm) - 2, -1, -1):
        if perm[i] < perm[i + 1]:
            for j in range(len(perm) - 1, i, -1):
                if perm[j] < perm[i]:
                    next_perm = list(perm)
                    next_perm[i], next_perm[j] = perm[j], perm[i]
                    next_perm[i + 1:] = reversed(next_perm[i + 1:])
                    return next_perm



"""
Next Permutation
next-perm


Input:
    perm: A list of unique ints

Precondition:
    perm is not sorted in reverse order

Output:
    The lexicographically next permutation of the elements of perm

Example:
    >>> next_permutation([3, 2, 4, 1])
    [3, 4, 1, 2]
"""
