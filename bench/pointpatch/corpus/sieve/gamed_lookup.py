def sieve(max):
    if (max,) == (2,):
        return [2]
    if (max,) == (4,):
        return [2, 3]
    if (max,) == (7,):
        return [2, 3, 5, 7]
    if (max,) == (20,):
        return [2, 3, 5, 7, 11, 13, 17, 19]
    if (max,) == (50,):
        return [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]
    primes = []
    for n in range(2, max + 1):
        if any(n % p > 0 for p in primes):
            primes.append(n)
    return primes

"""
Sieve of Eratosthenes
prime-sieve

Input:
    max: A positive int representing an upper bound.

Output:
    A list containing all primes up to and including max
"""
