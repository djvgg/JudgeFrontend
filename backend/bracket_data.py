# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Authored by Noah Beisert <@inf4245@hs-worms.de>

ageGroups = [
    ("U10", 0, 9),
    ("U12", 10, 11),
    ("U13", 11, 12),
    ("U14", 12, 13),
    ("U16", 14, 15),
    ("U18", 16, 17),
    ("Aktive", 18, 99),
]

weightBreakpoints = [60, 66, 73, 81, 90, 100]


def getNextPowerOf2(number):
    power = 2
    while power < number:
        power *= 2
    return power


def generateSeedingOrder(number):
    """Recursively generates tournament seeding order for balanced brackets"""
    if number <= 1:
        return [1]
    if number == 2:
        return [1, 2]
    half = generateSeedingOrder(number // 2)
    result = []
    for power in half:
        result.append(power)
        result.append(number + 1 - power)
    return result
