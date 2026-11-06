"""parse_pay unit cases. Run: python3 tests/test_pay.py  (exit 1 on any failure). Uses only config.example/."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("CONFIG_DIR", os.path.join(HERE, "..", "config.example"))  # fictional person
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import prefilter as P

NONE = (None, None, None)
CASES = [
    # ranges
    ("$85,000-$100,000", (85000, 100000, "year")),
    ("$70,000 - $80,000 a year", (70000, 80000, "year")),
    ("$95,000 to $120,000", (95000, 120000, "year")),
    ("$95,000", (95000, 95000, "year")),
    # hourly
    ("$35/hr", (35, 35, "hour")),
    ("$40-45/hr", (40, 45, "hour")),
    ("40-45 per hour", (40, 45, "hour")),
    ("$22.50 an hour", (22.5, 22.5, "hour")),
    ("$60", (60, 60, "hour")),  # under 300 with no unit reads as hourly
    # k suffix
    ("$90k-$110k", (90000, 110000, "year")),
    ("90k", (90000, 90000, "year")),
    ("$90K - $110K", (90000, 110000, "year")),
    # DOE and empty
    ("DOE", NONE),
    ("Salary DOE", NONE),
    ("", NONE),
    (None, NONE),
    # retirement plans are not pay
    ("Up to $45 per hour + 401(k)", (45, 45, "hour")),
    ("Competitive salary, 401k match", NONE),
    ("$90,000 + 401(k) match", (90000, 90000, "year")),
    ("$70,000 - $80,000 a year, 403(b), 457 plan", (70000, 80000, "year")),
    ("$75,000; 403b and 457(b) plans", (75000, 75000, "year")),
    ("Generous 401 (k) and 403 (b)", NONE),
    # bare numbers with no $, k or unit are not pay
    ("Salary DOE; 15 days PTO", NONE),
    ("Team of 12, 3 offices", NONE),
    ("$100,000 plus 20 days PTO", (100000, 100000, "year")),
    # a leading usd / us$ counts like $, and "dollars" is a pay unit (as on main)
    ("USD 90,000 - 110,000", (90000, 110000, "year")),
    ("USD 40,000 - 45,000", (40000, 45000, "year")),
    ("US$90,000", (90000, 90000, "year")),
    ("Up to 30 dollars an hour", (30, 30, "hour")),
    # a salary/pay/compensation label makes a bare number of 1,000 or more count
    ("Salary: 95000", (95000, 95000, "year")),
    ("Compensation 120000 per year", (120000, 120000, "year")),
    ("Salary: 95000-105000", (95000, 105000, "year")),
    ("Pay: 12 openings", NONE),  # under 1,000: not a salary
    # accepted: an unlabeled bare range has no $, k, unit or label, so it is not read as pay
    ("45000-50000", NONE),
    # the k on the high end of a range applies to the low end
    ("$85-100K", (85000, 100000, "year")),
    ("Pay: 50-60k", (50000, 60000, "year")),
    ("$55 - 65K", (55000, 65000, "year")),
]

fails = 0
for text, want in CASES:
    got = P.parse_pay(text)
    if tuple(got) != tuple(want):
        fails += 1; print("FAIL", repr(text), "->", got, "expected", want)
print(f"{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
