"""
Step 2 - Synthetic data: what the bank's core AML system (Actimize/FICO) would hand us.

Builds a realistic Saudi retail + SME banking book:
  customers, counterparties, 90 days of normal transaction history,
  and 1,000 AML alerts already raised by the "core system".

~70% of alerts are false positives (legitimate activity that tripped a rule),
~30% are genuine risk. Each alert comes from a named scenario so we can explain
WHY it was flagged and later measure triage accuracy against ground truth.

~15% of alerts are deliberately ambiguous (missing descriptions on legit
activity, plausible cover stories on risky activity) so the triage engine
can't win just by reading the memo field.

Usage:  python -m scripts.generate_data            (seed 42, 1000 alerts)
        python -m scripts.generate_data --alerts 200 --seed 7
"""
import argparse
import csv
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from scripts.config import DATA_DIR
from scripts.db import get_conn, init_db

RIYADH = timezone(timedelta(hours=3))
HISTORY_START = datetime(2026, 7, 1, tzinfo=RIYADH)
HISTORY_END = datetime(2026, 9, 30, 23, 59, tzinfo=RIYADH)
ALERT_WINDOW_START = datetime(2026, 9, 1, tzinfo=RIYADH)

SAUDI_MALE = ["Mohammed", "Abdullah", "Fahad", "Khalid", "Saad", "Faisal", "Sultan", "Turki",
              "Nasser", "Omar", "Yousef", "Abdulaziz", "Majed", "Bandar", "Nawaf", "Saleh"]
SAUDI_FEMALE = ["Noura", "Sara", "Reem", "Hessa", "Lama", "Maha", "Amal", "Abeer", "Dana", "Haya"]
SAUDI_FAMILY = ["Al-Qahtani", "Al-Otaibi", "Al-Harbi", "Al-Ghamdi", "Al-Zahrani", "Al-Shehri",
                "Al-Dosari", "Al-Mutairi", "Al-Rashid", "Al-Anazi", "Al-Shammari", "Al-Subaie"]
EXPAT = {
    "IN": (["Rajesh Kumar", "Arun Nair", "Suresh Pillai", "Vijay Sharma", "Anil Menon"], "Indian"),
    "PK": (["Imran Khan", "Asif Mahmood", "Bilal Ahmed", "Usman Tariq", "Zubair Ali"], "Pakistani"),
    "PH": (["Jose Santos", "Maria Reyes", "Mark dela Cruz", "Ana Garcia", "John Bautista"], "Filipino"),
    "EG": (["Ahmed Hassan", "Mahmoud Ibrahim", "Mostafa Adel", "Tamer Saeed", "Hany Fouad"], "Egyptian"),
    "BD": (["Rahim Uddin", "Karim Hossain", "Jamal Miah", "Sohel Rana", "Rafiq Islam"], "Bangladeshi"),
}
CITIES = ["Riyadh"] * 5 + ["Jeddah"] * 3 + ["Dammam"] * 2 + ["Mecca", "Medina", "Khobar", "Abha", "Tabuk", "Buraidah"]
BIZ_WORDS = ["Al-Noor", "Al-Faisal", "Najd", "Al-Waha", "Al-Masa", "Tihama", "Al-Yamama", "Hijaz",
             "Al-Rawabi", "Al-Safwa", "Al-Jazeera", "Al-Ufuq", "Al-Bayan", "Al-Nakheel"]
BIZ_SECTORS = [("Trading Est.", "Wholesale trading"), ("Contracting Co.", "Construction"),
               ("Restaurants Co.", "Food & beverage"), ("Retail Co.", "Retail"),
               ("Logistics Co.", "Logistics"), ("IT Solutions", "Technology services")]
MERCHANTS = ["Panda Retail", "Othaim Markets", "Jarir Bookstore", "Extra Stores", "Nahdi Pharmacy",
             "Al-Baik", "Danube", "Tamimi Markets", "SACO", "Herfy", "Kudu", "Saudia Airlines",
             "Flynas", "Almosafer Travel", "STC", "Mobily", "SEC Electricity"]
HIGH_RISK_JURIS = ["IR", "KP", "MM"]          # FATF call-for-action list
MEDIUM_RISK_FOREIGN = ["AE", "TR", "CN", "HK"]


class Gen:
    def __init__(self, seed: int):
        self.r = random.Random(seed)
        self.customers, self.cps, self.txns, self.alerts = [], [], [], []
        self.cust_meta = {}           # customer_id -> dict of helper info (not stored)
        self._n = Counter()

    # ---------- ids / helpers ----------
    def _id(self, prefix, width=5, start=10000):
        self._n[prefix] += 1
        return f"{prefix}-{start + self._n[prefix]:0{width}d}"

    def ts(self, dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    def rand_time(self, start, end, business_hours=False):
        dt = start + timedelta(seconds=self.r.randint(0, int((end - start).total_seconds())))
        if business_hours:
            dt = dt.replace(hour=self.r.randint(9, 16), minute=self.r.randint(0, 59))
            while dt.weekday() in (4, 5):     # Fri/Sat weekend in KSA
                dt -= timedelta(days=1)
        return dt

    def amt(self, lo, hi, round_to=None):
        v = self.r.uniform(lo, hi)
        return float(round(v / round_to) * round_to) if round_to else round(v, 2)

    # ---------- counterparties ----------
    def cp(self, name, cp_type, country, registered=False, risk="Low", watchlist=False):
        cp_id = self._id("CP", start=20000)
        self.cps.append(dict(counterparty_id=cp_id, name=name, counterparty_type=cp_type,
                             country=country, is_registered_business=int(registered),
                             risk_rating=risk, on_watchlist=int(watchlist)))
        return cp_id

    def saudi_person(self):
        first = self.r.choice(SAUDI_MALE + SAUDI_FEMALE)
        return f"{first} {self.r.choice(SAUDI_FAMILY)}"

    def biz_name(self):
        suffix, sector = self.r.choice(BIZ_SECTORS)
        return f"{self.r.choice(BIZ_WORDS)} {suffix}", sector

    def build_shared_cps(self):
        self.merchants = [self.cp(m, "business", "SA", True, "Low") for m in MERCHANTS]
        self.employers = [self.cp(n, "business", "SA", True, "Low") for n in
                          ["Ministry of Education", "Ministry of Health", "Saudi Aramco", "SABIC",
                           "Al Rajhi Bank", "STC Group", "Riyadh Municipality", "Almarai",
                           "Nesma & Partners", "Saudi Binladin Group", "Bupa Arabia", "Elm Company"]]
        self.car_dealers = [self.cp(n, "business", "SA", True, "Low") for n in
                            ["Abdul Latif Jameel Motors", "Aljomaih Automotive", "Petromin Nissan"]]
        self.developers = [self.cp(n, "business", "SA", True, "Low") for n in
                           ["ROSHN Real Estate", "Dar Al Arkan", "Retal Urban Development"]]
        self.universities = [self.cp(n, "business", "SA", True, "Low") for n in
                             ["Prince Sultan University", "Alfaisal University", "Dar Al-Hekma University"]]

    # ---------- customers ----------
    def add_customer(self, kind, dormant=False):
        r = self.r
        cid = self._id("CUST")
        onboarded = HISTORY_START - timedelta(days=r.randint(30, 3650))
        meta = dict(kind=kind, dormant=dormant)
        if kind == "saudi_salaried":
            name, nat, ctype = self.saudi_person(), "Saudi", "individual"
            occ = r.choice(["Government employee", "Engineer", "Teacher", "Bank employee", "Doctor", "Sales manager"])
            salary = self.amt(8000, 40000, 100)
            meta.update(salary=salary, employer=r.choice(self.employers))
            vol, risk = salary * 1.1, r.choices(["Low", "Medium"], [85, 15])[0]
        elif kind == "expat":
            country = r.choice(list(EXPAT))
            names, nat = EXPAT[country]
            name, ctype = r.choice(names), "individual"
            occ = r.choice(["Construction worker", "Driver", "Technician", "Nurse", "Accountant", "Cook"])
            salary = self.amt(2500, 12000, 50)
            fam_name = f"{name.split()[0]} family ({r.choice(['wife', 'father', 'brother', 'mother'])})"
            meta.update(salary=salary, employer=r.choice(self.employers), home=country,
                        family_cp=self.cp(fam_name, "individual", country, False, "Low"))
            vol, risk = salary, r.choices(["Low", "Medium"], [80, 20])[0]
        elif kind == "student":
            name, nat, ctype = self.saudi_person(), "Saudi", "individual"
            occ, vol, risk = "University student", self.amt(900, 2500, 50), "Low"
            onboarded = HISTORY_START - timedelta(days=r.randint(30, 900))
            meta.update(allowance=vol, family_cp=self.cp(self.saudi_person(), "individual", "SA"))
        elif kind == "hnw":
            name, nat, ctype = self.saudi_person(), "Saudi", "individual"
            occ, vol = "Business owner", self.amt(80000, 400000, 1000)
            risk = r.choices(["Low", "Medium", "High"], [40, 45, 15])[0]
            meta.update(income=vol)
        else:  # sme
            name, occ = self.biz_name()
            nat, ctype, vol = "Saudi", "business", self.amt(100000, 1500000, 1000)
            risk = r.choices(["Low", "Medium"], [75, 25])[0]
            suppliers = []
            for _ in range(r.randint(2, 4)):
                if r.random() < 0.25:
                    sname, _ = self.biz_name()
                    c = r.choice(MEDIUM_RISK_FOREIGN)
                    suppliers.append(self.cp(f"{sname.split()[0]} Industries {c}", "business", c, True, "Medium"))
                else:
                    sname, _ = self.biz_name()
                    suppliers.append(self.cp(sname, "business", "SA", True, r.choice(["Low", "Low", "Medium"])))
            clients = [self.cp(self.biz_name()[0], "business", "SA", True, "Low") for _ in range(3)]
            meta.update(suppliers=suppliers, clients=clients, supplier_avg=vol / 6)
        is_pep = int(kind == "hnw" and r.random() < 0.15)
        self.customers.append(dict(customer_id=cid, full_name=name, customer_type=ctype, nationality=nat,
                                   city=r.choice(CITIES), occupation_or_sector=occ,
                                   onboarded_date=onboarded.date().isoformat(), kyc_risk_rating=risk,
                                   is_pep=is_pep, avg_monthly_volume_sar=round(vol, 2)))
        self.cust_meta[cid] = meta
        return cid

    # ---------- transactions ----------
    def txn(self, cid, cp_id, amount, direction, channel, dt, desc=None):
        tid = self._id("TXN", width=7, start=1000000)
        self.txns.append(dict(transaction_id=tid, customer_id=cid, counterparty_id=cp_id,
                              amount_sar=round(amount, 2), direction=direction, channel=channel,
                              timestamp=self.ts(dt), description=desc))
        return tid

    def history(self, cid):
        """90 days of boring, normal behaviour for this customer."""
        r, m = self.r, self.cust_meta[cid]
        end = HISTORY_START + timedelta(days=20) if m["dormant"] else HISTORY_END
        months = [HISTORY_START.replace(month=mo) for mo in (7, 8, 9)]
        for month in months:
            if month > end:
                break
            if m["kind"] in ("saudi_salaried", "expat"):
                pay = month.replace(day=27, hour=r.randint(6, 9))
                if pay <= end:
                    self.txn(cid, m["employer"], m["salary"], "inbound", "SARIE", pay, "Salary")
            if m["kind"] == "expat":
                d = month.replace(day=r.randint(1, 5), hour=r.randint(18, 22))
                if d <= end:
                    self.txn(cid, m["family_cp"], m["salary"] * r.uniform(0.4, 0.7), "outbound",
                             "mobile", d, "Family support")
            if m["kind"] == "student":
                d = month.replace(day=r.randint(1, 3), hour=r.randint(10, 20))
                if d <= end:
                    self.txn(cid, m["family_cp"], m["allowance"], "inbound", "mobile", d, "Monthly allowance")
            if m["kind"] == "sme":
                for s in m["suppliers"]:
                    d = self.rand_time(month, month + timedelta(days=27), business_hours=True)
                    if d <= end:
                        self.txn(cid, s, m["supplier_avg"] * r.uniform(0.6, 1.4) / len(m["suppliers"]),
                                 "outbound", "SWIFT" if self._cp_country(s) != "SA" else "SARIE", d,
                                 f"Invoice INV-2026-{r.randint(1000, 9999)}")
                for c in m["clients"]:
                    d = self.rand_time(month, month + timedelta(days=27), business_hours=True)
                    if d <= end:
                        self.txn(cid, c, m["supplier_avg"] * r.uniform(0.8, 1.6), "inbound", "SARIE", d,
                                 "Payment for goods")
            if m["kind"] == "hnw":
                d = self.rand_time(month, month + timedelta(days=27), business_hours=True)
                if d <= end:
                    self.txn(cid, self.r.choice(self.employers), m["income"] * r.uniform(0.7, 1.3),
                             "inbound", "SARIE", d, "Dividend distribution")
            # everyday card spend (everyone except SMEs)
            if m["kind"] != "sme":
                cap = {"student": 300, "expat": 400, "saudi_salaried": 1500, "hnw": 8000}[m["kind"]]
                for _ in range(r.randint(4, 10)):
                    d = self.rand_time(month, month + timedelta(days=29))
                    if d <= end:
                        self.txn(cid, r.choice(self.merchants), self.amt(20, cap), "outbound", "card", d)

    def _cp_country(self, cp_id):
        return next(c["country"] for c in self.cps if c["counterparty_id"] == cp_id)

    # ---------- alerts ----------
    def alert(self, tid, dt, reason, truth, scenario):
        r = self.r
        sev_w = [50, 35, 15] if truth == "genuine_risk" else [25, 45, 30]   # core-system severity is noisy
        aid = f"ALT-2026-{len(self.alerts) + 1:05d}"
        self.alerts.append(dict(alert_id=aid, transaction_id=tid, source_system="Actimize-SIM",
                                alert_reason=reason, alert_severity=r.choices(["High", "Medium", "Low"], sev_w)[0],
                                created_at=self.ts(dt + timedelta(minutes=r.randint(1, 15))),
                                status="new", ground_truth=truth, _scenario=scenario))

    def pick(self, kinds, dormant=None):
        pool = [c for c, m in self.cust_meta.items()
                if m["kind"] in kinds and (dormant is None or m["dormant"] == dormant)]
        return self.r.choice(pool)

    def when(self):
        return self.rand_time(ALERT_WINDOW_START, HISTORY_END - timedelta(days=1))

    def maybe(self, desc, truth):
        """Ambiguity: strip legit descriptions / add cover stories to risky ones."""
        if self.r.random() < 0.15:
            if truth == "false_positive":
                return None
            return self.r.choice(["Family support", "Business payment", "Loan repayment", "Gift"])
        return desc

    # --- false positives (legit activity that tripped a rule) ---
    def fp_bonus(self):
        cid = self.pick(["saudi_salaried"], dormant=False); m = self.cust_meta[cid]
        dt = self.when().replace(hour=self.r.randint(7, 10))
        desc = self.r.choice(["Annual performance bonus", "End of service benefits", "Housing allowance - annual"])
        tid = self.txn(cid, m["employer"], m["salary"] * self.r.uniform(3, 8), "inbound", "SARIE", dt,
                       self.maybe(desc, "false_positive"))
        self.alert(tid, dt, "amount_spike", "false_positive", "fp_bonus")

    def fp_supplier(self):
        cid = self.pick(["sme"]); m = self.cust_meta[cid]
        s = self.r.choice([x for x in m["suppliers"] if self._cp_country(x) == "SA"] or m["suppliers"])
        dt = self.rand_time(ALERT_WINDOW_START, HISTORY_END - timedelta(days=1), business_hours=True)
        tid = self.txn(cid, s, m["supplier_avg"] * self.r.uniform(2, 5), "outbound", "SARIE", dt,
                       self.maybe(f"Invoice INV-2026-{self.r.randint(1000, 9999)} - quarterly stock order", "false_positive"))
        self.alert(tid, dt, "amount_spike", "false_positive", "fp_supplier")

    def fp_remittance(self):
        cid = self.pick(["expat"], dormant=False); m = self.cust_meta[cid]
        dt = self.when().replace(hour=self.r.randint(18, 22))
        desc = self.r.choice(["Family support", "Eid money for family", "School fees for children"])
        tid = self.txn(cid, m["family_cp"], m["salary"] * self.r.uniform(0.5, 1.2), "outbound", "mobile", dt,
                       self.maybe(desc, "false_positive"))
        self.alert(tid, dt, "high_risk_country", "false_positive", "fp_remittance")

    def fp_velocity(self):
        cid = self.pick(["saudi_salaried", "hnw"], dormant=False)
        day = self.when().replace(hour=9)
        travel = self.r.random() < 0.5
        cps = [c for c in self.merchants] if not travel else \
              [c for c, n in zip(self.merchants, MERCHANTS) if n in ("Saudia Airlines", "Flynas", "Almosafer Travel")]
        tid = None
        for i in range(self.r.randint(8, 14)):
            dt = day + timedelta(minutes=35 * i + self.r.randint(0, 20))
            tid = self.txn(cid, self.r.choice(cps), self.amt(150, 2500), "outbound", "card", dt,
                           self.maybe("Family holiday booking" if travel else None, "false_positive"))
        self.alert(tid, dt, "velocity", "false_positive", "fp_velocity")

    def fp_big_purchase(self):
        cid = self.pick(["saudi_salaried", "hnw"], dormant=False)
        car = self.r.random() < 0.6
        dt = self.rand_time(ALERT_WINDOW_START, HISTORY_END - timedelta(days=1), business_hours=True)
        cp = self.r.choice(self.car_dealers if car else self.developers)
        amount = self.amt(90000, 280000, 500) if car else self.amt(150000, 450000, 1000)
        desc = self.r.choice(["Car purchase - Toyota Land Cruiser", "Car purchase - Hyundai Tucson"]) if car \
            else "Down payment - apartment, ROSHN Sedra"
        tid = self.txn(cid, cp, amount, "outbound", "SARIE", dt, self.maybe(desc, "false_positive"))
        self.alert(tid, dt, "amount_spike", "false_positive", "fp_big_purchase")

    def fp_dormant_return(self):
        cid = self.pick(["saudi_salaried", "student"], dormant=True)
        dt = self.rand_time(ALERT_WINDOW_START, HISTORY_END - timedelta(days=1), business_hours=True)
        if self.cust_meta[cid]["kind"] == "student":
            cp, amount, desc = self.r.choice(self.universities), self.amt(8000, 25000, 500), "Tuition fees - fall semester"
        else:
            cp, amount, desc = self.r.choice(self.merchants), self.amt(200, 3000), "Back from summer abroad"
        tid = self.txn(cid, cp, amount, "outbound", "mobile", dt, self.maybe(desc, "false_positive"))
        self.alert(tid, dt, "dormant_reactivation", "false_positive", "fp_dormant_return")

    def fp_foreign_supplier(self):
        cid = self.pick(["sme"]); m = self.cust_meta[cid]
        foreign = [x for x in m["suppliers"] if self._cp_country(x) != "SA"]
        if not foreign:
            c = self.r.choice(MEDIUM_RISK_FOREIGN)
            s = self.cp(f"{self.r.choice(BIZ_WORDS)} Industries {c}", "business", c, True, "Medium")
            m["suppliers"].append(s)
            # give it real history so the relationship is established
            for mo in (7, 8):
                d = self.rand_time(HISTORY_START.replace(month=mo), HISTORY_START.replace(month=mo, day=27), True)
                self.txn(cid, s, m["supplier_avg"] * self.r.uniform(0.3, 0.6), "outbound", "SWIFT", d,
                         f"Invoice INV-2026-{self.r.randint(1000, 9999)}")
        else:
            s = self.r.choice(foreign)
        dt = self.rand_time(ALERT_WINDOW_START, HISTORY_END - timedelta(days=1), business_hours=True)
        tid = self.txn(cid, s, m["supplier_avg"] * self.r.uniform(0.8, 2.0), "outbound", "SWIFT", dt,
                       self.maybe(f"Invoice INV-2026-{self.r.randint(1000, 9999)} - raw materials import", "false_positive"))
        self.alert(tid, dt, "counterparty_risk", "false_positive", "fp_foreign_supplier")

    # --- genuine risk ---
    def gr_structuring(self):
        cid = self.pick(["saudi_salaried", "expat", "hnw"], dormant=False)
        start = self.when()
        cp = self.cp(self.saudi_person(), "individual", "SA", False, "Medium")
        tid = None
        for i in range(self.r.randint(4, 7)):
            dt = start + timedelta(hours=self.r.randint(3, 14) * i)
            tid = self.txn(cid, cp, self.amt(9000, 9950, 10), "outbound",
                           self.r.choice(["branch", "mobile"]), dt, self.maybe(None, "genuine_risk"))
        self.alert(tid, dt, "structuring", "genuine_risk", "gr_structuring")

    def gr_mule(self):
        cid = self.pick(["student", "expat"], dormant=False)
        start = self.when().replace(hour=self.r.randint(0, 10))
        for i in range(self.r.randint(5, 10)):
            src = self.cp(self.saudi_person(), "individual", "SA", False, "Low")
            self.txn(cid, src, self.amt(2000, 9000, 50), "inbound", "mobile", start + timedelta(minutes=40 * i))
        dest_c = self.r.choice(MEDIUM_RISK_FOREIGN + HIGH_RISK_JURIS[:1])
        dest = self.cp(self.r.choice(["Global Trade FZE", "Rapid Exchange Ltd", "Horizon General Trading"]),
                       "business", dest_c, False, "High")
        dt = start + timedelta(hours=self.r.randint(6, 20))
        tid = self.txn(cid, dest, self.amt(25000, 60000, 100), "outbound", "SWIFT", dt, self.maybe(None, "genuine_risk"))
        self.alert(tid, dt, "velocity", "genuine_risk", "gr_mule")

    def gr_profile_mismatch(self):
        cid = self.pick(["student", "expat"], dormant=False)
        c = self.r.choice(MEDIUM_RISK_FOREIGN)
        src = self.cp(f"{self.r.choice(['Apex', 'Silverline', 'Orion', 'Meridian'])} Consulting {c}",
                      "business", c, False, "High")
        dt = self.when()
        tid = self.txn(cid, src, self.amt(150000, 400000, 1000), "inbound", "SWIFT", dt,
                       self.maybe("Consulting services", "genuine_risk"))
        self.alert(tid, dt, "amount_spike", "genuine_risk", "gr_profile_mismatch")

    def gr_high_risk_new(self):
        cid = self.pick(["saudi_salaried", "hnw", "sme"], dormant=False)
        c = self.r.choice(HIGH_RISK_JURIS)
        cp = self.cp(f"{self.r.choice(['Pars', 'Golden', 'Eastern'])} {self.r.choice(['Trading', 'Holding'])} Co.",
                     "business", c, False, "High")
        dt = self.when()
        tid = self.txn(cid, cp, self.amt(20000, 200000, 5000), "outbound", "SWIFT", dt, self.maybe(None, "genuine_risk"))
        self.alert(tid, dt, "high_risk_country", "genuine_risk", "gr_high_risk_new")

    def gr_dormant_wire(self):
        cid = self.pick(["saudi_salaried", "student"], dormant=True)
        c = self.r.choice(MEDIUM_RISK_FOREIGN + HIGH_RISK_JURIS)
        src = self.cp(self.saudi_person(), "individual", "SA", False, "Low")
        dest = self.cp(f"{self.r.choice(['Nova', 'Atlas', 'Zenith'])} Ventures", "business", c, False, "High")
        dt = self.when()
        self.txn(cid, src, self.amt(80000, 150000, 1000), "inbound", "SARIE", dt - timedelta(hours=self.r.randint(2, 30)))
        tid = self.txn(cid, dest, self.amt(75000, 145000, 1000), "outbound", "SWIFT", dt, self.maybe(None, "genuine_risk"))
        self.alert(tid, dt, "dormant_reactivation", "genuine_risk", "gr_dormant_wire")

    def gr_watchlist(self):
        cid = self.pick(["saudi_salaried", "hnw", "sme", "expat"], dormant=False)
        cp = self.cp(self.r.choice(["Al-Hadaf Charity Foundation", "Crescent Relief Org", "Sahara Exchange House"]),
                     "business", self.r.choice(["SA", "AE", "TR"]), False, "High", watchlist=True)
        dt = self.when()
        tid = self.txn(cid, cp, self.amt(5000, 80000, 500), "outbound", "SARIE", dt,
                       self.maybe("Donation", "genuine_risk"))
        self.alert(tid, dt, "counterparty_risk", "genuine_risk", "gr_watchlist")

    SCENARIOS = [  # (method, weight)  -> FP 70 / GR 30
        ("fp_bonus", 10), ("fp_supplier", 12), ("fp_remittance", 12), ("fp_velocity", 10),
        ("fp_big_purchase", 10), ("fp_dormant_return", 6), ("fp_foreign_supplier", 10),
        ("gr_structuring", 7), ("gr_mule", 7), ("gr_profile_mismatch", 5),
        ("gr_high_risk_new", 5), ("gr_dormant_wire", 3), ("gr_watchlist", 3),
    ]

    def run(self, n_alerts):
        self.build_shared_cps()
        mix = [("saudi_salaried", 200, 40), ("expat", 150, 0), ("student", 60, 30), ("hnw", 30, 0), ("sme", 120, 0)]
        for kind, n, n_dormant in mix:
            for i in range(n):
                self.add_customer(kind, dormant=i < n_dormant)
        for cid in list(self.cust_meta):
            self.history(cid)
        names, weights = zip(*self.SCENARIOS)
        for name in self.r.choices(names, weights, k=n_alerts):
            getattr(self, name)()
        self.txns.sort(key=lambda t: t["timestamp"])
        return self


def write_csv(path, rows, drop=()):
    rows = [{k: v for k, v in row.items() if k not in drop} for row in rows]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def load_db(g: Gen):
    init_db(reset=True)
    with get_conn() as conn:
        for table, rows in [("customers", g.customers), ("counterparties", g.cps),
                            ("transactions", g.txns), ("alerts", g.alerts)]:
            rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
            cols = list(rows[0])
            conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [tuple(r[c] for c in cols) for r in rows])
        conn.execute("INSERT INTO audit_log(actor, event_type, details) VALUES ('system','data_loaded',?)",
                     (f'{{"alerts": {len(g.alerts)}, "transactions": {len(g.txns)}}}',))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerts", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    g = Gen(a.seed).run(a.alerts)
    DATA_DIR.mkdir(exist_ok=True)
    write_csv(DATA_DIR / "customers.csv", g.customers)
    write_csv(DATA_DIR / "counterparties.csv", g.cps)
    write_csv(DATA_DIR / "transactions.csv", g.txns)
    # What the core AML system sends us: NO ground truth.
    write_csv(DATA_DIR / "alerts.csv", g.alerts, drop=("ground_truth", "_scenario"))
    # Kept separately, used only to score triage accuracy.
    write_csv(DATA_DIR / "alert_labels.csv",
              [{"alert_id": x["alert_id"], "ground_truth": x["ground_truth"], "scenario": x["_scenario"]} for x in g.alerts])
    load_db(g)

    truth = Counter(x["ground_truth"] for x in g.alerts)
    print(f"\nCustomers:      {len(g.customers):>6}")
    print(f"Counterparties: {len(g.cps):>6}")
    print(f"Transactions:   {len(g.txns):>6}")
    print(f"Alerts:         {len(g.alerts):>6}  "
          f"(false positive {truth['false_positive'] / len(g.alerts):.0%}, genuine {truth['genuine_risk'] / len(g.alerts):.0%})")
    print("\nBy scenario:")
    for s, n in sorted(Counter(x["_scenario"] for x in g.alerts).items()):
        print(f"  {s:<22}{n:>5}")
    print("\nBy alert reason (what the core system says):")
    for s, n in sorted(Counter(x["alert_reason"] for x in g.alerts).items()):
        print(f"  {s:<22}{n:>5}")


if __name__ == "__main__":
    main()
