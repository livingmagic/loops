import subprocess
from pathlib import Path


def create_fixture(root):
    repo = Path(root) / "examples" / "checkout-service"
    if not (repo / ".git").exists():
        (repo / "tests").mkdir(parents=True, exist_ok=True)
        (repo / "checkout.py").write_text('''def total_price(quantity, unit_price, discount=0):
    """Return total after a percentage discount."""
    return round(quantity * unit_price - discount, 2)
''')
        (repo / "tests" / "test_checkout.py").write_text('''import unittest
from checkout import total_price

class CheckoutTests(unittest.TestCase):
    def test_no_discount(self):
        self.assertEqual(total_price(2, 100), 200)
    def test_zero_quantity(self):
        self.assertEqual(total_price(0, 100), 0)
''')
        (repo / "README.md").write_text('''# Checkout Service
Python standard-library demo service. Run tests: python -m unittest discover -s tests -p 'test_*.py'
`discount` is a percentage between 0 and 100. A negative quantity is invalid and must raise ValueError.
Add a regression test reproducing the log before changing checkout.py. Keep existing tests.
No remote repository is configured; deliver a local patch and test report.
''')
        (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Loops Local Test", "-c", "user.email=loops-local@example.invalid", "-c", "core.hooksPath=/dev/null", "commit", "-m", "Initialize local checkout sample"], check=True, capture_output=True)
    logs = Path(root) / "examples" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "discount-error.log").write_text('2026-10-23T09:00:00+08:00 ERROR checkout total mismatch request_id=sample-1 quantity=2 unit_price=100 discount=10 expected_total=180 actual_total=190\n')
    (logs / "quantity-error.log").write_text('2026-10-23T10:00:00+08:00 ERROR checkout invalid order accepted request_id=sample-2 quantity=-1 unit_price=100 discount=0 expected=ValueError actual_total=-100\n')
    return {"resources": [{"id": "project", "label": "故障样例仓库", "kind": "git", "path": str(repo)},
                          {"id": "quantityLog", "label": "负数数量校验日志", "kind": "file", "path": str(logs / "quantity-error.log")}],
            "inputDefaults": {"source": str(logs / "discount-error.log")}, "secondInputPath": str(logs / "quantity-error.log")}


def create_sales_fixture(root):
    directory = Path(root) / "examples" / "sales"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "README.md").write_text("销售明细 CSV：date、product、quantity、unit_price、status。仅统计 status=paid；金额为 quantity*unit_price，使用 Decimal 避免金额误差。取消订单不计入。按商品汇总销售额与数量，输出经营简报。输入不合法时明确报错，不默默忽略。只使用 Python 标准库。\n")
    (directory / "sales-a.csv").write_text("date,product,quantity,unit_price,status\n2026-10-01,课程A,2,100.00,paid\n2026-10-02,课程B,1,60.50,paid\n2026-10-03,课程A,1,100.00,cancelled\n")
    (directory / "sales-b.csv").write_text("date,product,quantity,unit_price,status\n2026-10-04,课程A,3,100.00,paid\n2026-10-05,课程B,2,60.50,paid\n2026-10-06,课程B,1,60.50,cancelled\n")
    return {"resources": [{"id": "project", "label": "销售资料", "kind": "directory", "path": str(directory)}], "inputDefaults": {"source": str(directory / "sales-a.csv")}, "secondInputPath": str(directory / "sales-b.csv")}
