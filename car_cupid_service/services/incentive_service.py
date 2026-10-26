"""Salesperson incentives from submitted invoice lines (blueprint §11).

* Item incentive first: a line whose item carries Item.custom_sales_incentive earns
  qty x that amount (captured on the job/memo line when the item was added). It replaces the rules
  for that line, so an item is never paid twice.
* Otherwise the most specific Service Incentive Rule applies.

* Base = line net amount excluding GST (`base_net_amount`), after discounts.
* The matching rule and its rate are snapshotted on the ledger row at submission.
* Credit notes produce negative Reversal rows automatically (their amounts are negative).
* Cancelling an invoice posts mirror-image Reversal rows; nothing is deleted.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

SPECIFICITY = (
	("sales_person", 16),
	("item_code", 8),
	("item_group", 4),
	("item_type", 2),
	("for_company", 1),
)


def _rules():
	return frappe.get_all(
		"Service Incentive Rule",
		filters={"enabled": 1},
		fields=[
			"name",
			"basis",
			"rate",
			"priority",
			"for_company",
			"sales_person",
			"item_code",
			"item_group",
			"item_type",
			"valid_from",
			"valid_upto",
		],
	)


def _item_group_ancestors(item_group: str | None) -> set:
	if not item_group:
		return set()
	lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"]) or (None, None)
	if lft is None:
		return {item_group}
	return set(frappe.get_all("Item Group", filters={"lft": ("<=", lft), "rgt": (">=", rgt)}, pluck="name"))


def match_rule(rules, *, company, sales_person, item_code, item_group, item_type, posting_date):
	groups = _item_group_ancestors(item_group)
	best, best_key = None, None
	date = getdate(posting_date)
	for r in rules:
		if r.valid_from and getdate(r.valid_from) > date:
			continue
		if r.valid_upto and getdate(r.valid_upto) < date:
			continue
		if r.for_company and r.for_company != company:
			continue
		if r.sales_person and r.sales_person != sales_person:
			continue
		if r.item_code and r.item_code != item_code:
			continue
		if r.item_group and r.item_group not in groups:
			continue
		if r.item_type and r.item_type != item_type:
			continue
		score = sum(weight for field, weight in SPECIFICITY if r.get(field))
		key = (score, cint(r.priority))
		if best_key is None or key > best_key:
			best, best_key = r, key
	return best


def compute_amount(rule, base: float, qty: float, item_type: str | None) -> float:
	if rule.basis == "Percentage of Net Sales":
		return flt(base * flt(rule.rate) / 100.0, 2)
	if rule.basis == "Fixed per Unit":
		return flt(qty * flt(rule.rate), 2)
	if rule.basis == "Fixed per Service Line":
		if item_type in ("Service", "Labour", "Package"):
			return flt(rule.rate) * (1 if qty >= 0 else -1)
		return 0.0
	return 0.0


ITEM_BASIS = "Item Incentive per Unit"


def _item_rate(si, d) -> float:
	"""Per-unit rate on the invoice row; a credit note without it uses the original invoice row's."""
	rate = flt(d.get("cc_incentive_rate"))
	if not rate and cint(si.is_return) and d.get("sales_invoice_item"):
		rate = flt(frappe.db.get_value("Sales Invoice Item", d.sales_invoice_item, "cc_incentive_rate"))
	return rate


def on_invoice_submit(si):
	lines = [d for d in si.items if d.get("cc_sales_person")]
	if not lines:
		return
	rules = _rules()
	for d in lines:
		item_type = d.get("cc_item_type")
		item_rate = _item_rate(si, d)
		if item_rate:
			_post(si, d, item_type, None, ITEM_BASIS, item_rate, flt(flt(d.qty) * item_rate, 2))
			continue
		rule = match_rule(
			rules,
			company=si.company,
			sales_person=d.cc_sales_person,
			item_code=d.item_code,
			item_group=d.item_group,
			item_type=item_type,
			posting_date=si.posting_date,
		)
		if not rule:
			continue
		amount = compute_amount(rule, flt(d.base_net_amount), flt(d.qty), item_type)
		if not amount:
			continue
		_post(si, d, item_type, rule.name, rule.basis, rule.rate, amount)


def _post(si, d, item_type, rule_name, basis, rate, amount):
	if not amount:
		return
	frappe.get_doc(
		{
			"doctype": "Service Incentive Ledger",
			"sales_person": d.cc_sales_person,
			"company": si.company,
			"posting_date": si.posting_date,
			"entry_type": "Reversal" if cint(si.is_return) else "Earned",
			"sales_invoice": si.name,
			"invoice_item": d.name,
			"against_invoice": si.return_against if cint(si.is_return) else None,
			"item_code": d.item_code,
			"item_group": d.item_group,
			"item_type": item_type,
			"qty": d.qty,
			"eligible_net_sales": d.base_net_amount,
			"incentive_rule": rule_name,
			"basis": basis,
			"rate": rate,
			"incentive_amount": amount,
			"remarks": _("Credit note against {0}").format(si.return_against) if cint(si.is_return) else None,
		}
	).insert(ignore_permissions=True)


def on_invoice_cancel(si):
	for e in frappe.get_all(
		"Service Incentive Ledger", filters={"sales_invoice": si.name}, fields=["*"], order_by="creation asc"
	):
		if frappe.db.exists(
			"Service Incentive Ledger",
			{
				"against_invoice": si.name,
				"invoice_item": e.invoice_item,
				"remarks": ("like", "Invoice cancelled%"),
			},
		):
			continue
		if e.remarks and e.remarks.startswith("Invoice cancelled"):
			continue
		frappe.get_doc(
			{
				"doctype": "Service Incentive Ledger",
				"sales_person": e.sales_person,
				"company": e.company,
				"posting_date": frappe.utils.nowdate(),
				"entry_type": "Reversal",
				"sales_invoice": si.name,
				"invoice_item": e.invoice_item,
				"against_invoice": si.name,
				"item_code": e.item_code,
				"item_group": e.item_group,
				"item_type": e.item_type,
				"qty": -flt(e.qty),
				"eligible_net_sales": -flt(e.eligible_net_sales),
				"incentive_rule": e.incentive_rule,
				"basis": e.basis,
				"rate": e.rate,
				"incentive_amount": -flt(e.incentive_amount),
				"remarks": _("Invoice cancelled — reverses {0}").format(e.name),
			}
		).insert(ignore_permissions=True)


def estimate_for_line(row, company: str, posting_date=None) -> float:
	"""Draft incentive for an unbilled line (shown separately from earned incentive)."""
	if not row.get("sales_person"):
		return 0.0
	if flt(row.get("sales_incentive_rate")):
		return flt(row.sales_incentive_rate) * (flt(row.qty) - flt(row.get("billed_qty")))
	item_group = frappe.db.get_value("Item", row.item_code, "item_group")
	rule = match_rule(
		_rules(),
		company=company,
		sales_person=row.sales_person,
		item_code=row.item_code,
		item_group=item_group,
		item_type=row.get("item_type"),
		posting_date=posting_date or frappe.utils.nowdate(),
	)
	if not rule:
		return 0.0
	unbilled = flt(row.qty) - flt(row.get("billed_qty"))
	return compute_amount(rule, flt(row.rate) * unbilled, unbilled, row.get("item_type"))
