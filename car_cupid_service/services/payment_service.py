"""Customer payment capture and allocation across company invoices.

One customer payment (possibly split across cash / UPI / card) is allocated to the separate company
invoices and posted as one Payment Entry per (invoice, mode) in that company's own books, using the
company's default account for the Mode of Payment. Nothing is ever posted across companies.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from car_cupid_service.services.settings import get_settings, is_supervisor

TOLERANCE = 0.5  # rupee rounding tolerance when comparing tendered amount with the bill


def _mode_types() -> dict:
	return dict(
		frappe.get_all("Mode of Payment", filters={"enabled": 1}, fields=["name", "type"], as_list=True)
	)


def clean_payments(payments) -> list[dict]:
	payments = frappe.parse_json(payments) if isinstance(payments, str) else (payments or [])
	types = _mode_types()
	merged: dict[str, dict] = {}
	for p in payments:
		p = frappe._dict(p)
		amount = flt(p.amount, 2)
		if amount == 0:
			continue
		if amount < 0:
			frappe.throw(_("Payment amounts cannot be negative"))
		if p.mode_of_payment not in types:
			frappe.throw(_("Mode of payment {0} is not enabled").format(p.mode_of_payment))
		ref = (p.reference_no or "").strip()
		if types[p.mode_of_payment] == "Bank" and not ref:
			frappe.throw(
				_("Enter the transaction / UTR reference for the {0} payment.").format(p.mode_of_payment),
				title=_("Reference Required"),
			)
		key = f"{p.mode_of_payment}|{ref}"
		if key in merged:
			merged[key]["amount"] += amount
		else:
			merged[key] = {
				"mode_of_payment": p.mode_of_payment,
				"amount": amount,
				"reference_no": ref or None,
			}
	return list(merged.values())


def validate_payment_cover(payments: list[dict], total: float, allow_credit: bool = False) -> float:
	"""Validate tendered vs bill; returns cash change due back to the customer."""
	types = _mode_types()
	paid = sum(flt(p["amount"]) for p in payments)
	cash = sum(flt(p["amount"]) for p in payments if types.get(p["mode_of_payment"]) == "Cash")
	if paid < total - TOLERANCE:
		credit_ok = cint(get_settings().allow_credit_sale) and is_supervisor() and allow_credit
		if not credit_ok:
			frappe.throw(
				_("Bill total is {0} but only {1} has been collected. Collect {2} more{3}.").format(
					frappe.format_value(total, "Currency"),
					frappe.format_value(paid, "Currency"),
					frappe.format_value(total - paid, "Currency"),
					_(" or ask a Billing Supervisor to bill on credit")
					if cint(get_settings().allow_credit_sale)
					else "",
				),
				title=_("Payment Short"),
			)
		return 0.0
	excess = flt(paid - total, 2)
	if excess > 0.005:
		if excess > cash + 0.005:
			frappe.throw(
				_(
					"Card / UPI / bank payments exceed the bill by {0}. Only cash can be over-tendered."
				).format(frappe.format_value(excess - cash, "Currency")),
				title=_("Overpayment"),
			)
		return excess
	return 0.0


def validate_mode_accounts(payments: list[dict], companies: list[str]):
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	missing = []
	for p in payments:
		for company in companies:
			try:
				acc = get_bank_cash_account(p["mode_of_payment"], company)
				if not (acc and acc.get("account")):
					missing.append((p["mode_of_payment"], company))
			except Exception:
				frappe.clear_messages()
				missing.append((p["mode_of_payment"], company))
	if missing:
		frappe.throw(
			_("Set the default account in Mode of Payment for:")
			+ "<br>"
			+ "<br>".join(f"• {m} → {c}" for m, c in missing),
			title=_("Payment Account Missing"),
		)


def usable_modes(companies: list[str]) -> list[dict]:
	"""Enabled modes that have a default account in every company of the bill; cash first."""
	if not companies:
		return []
	modes = frappe.get_all("Mode of Payment", filters={"enabled": 1}, fields=["name", "type"])
	accounts = frappe.get_all(
		"Mode of Payment Account",
		filters={
			"parenttype": "Mode of Payment",
			"company": ("in", companies),
			"default_account": ("is", "set"),
		},
		fields=["parent", "company"],
	)
	have: dict[str, set] = {}
	for a in accounts:
		have.setdefault(a.parent, set()).add(a.company)
	cash = get_settings().cash_mode_of_payment
	out = [m for m in modes if have.get(m.name, set()) >= set(companies)]
	order = {"Cash": 0, "Bank": 1, "General": 2, "Phone": 1}
	out.sort(key=lambda m: (0 if m.name == cash else 1, order.get(m.type, 3), m.name))
	return out


def get_open_session(user: str | None = None) -> str | None:
	return frappe.db.get_value(
		"Counter Session",
		{"cashier": user or frappe.session.user, "docstatus": 0},
		"name",
		order_by="creation desc",
	)


def resolve_session(source, payments: list[dict]) -> str | None:
	session = source.get("counter_session")
	if session and frappe.db.get_value("Counter Session", session, "docstatus") != 0:
		session = None
	if not session:
		session = get_open_session()
	if payments and not session and cint(get_settings().require_open_session_for_billing):
		frappe.throw(_("Open your counter session before collecting payment."), title=_("Counter Closed"))
	return session


# ----------------------------------------------------------------------------- allocation
def _distribute(pool: list[dict], targets: list[dict], policy: str) -> list[dict]:
	"""pool: [{mode_of_payment, reference_no, amount}], targets: [{sales_invoice, company, need}]"""
	allocations = []
	total_pool = sum(p["amount"] for p in pool)
	total_need = sum(t["need"] for t in targets)
	if total_pool <= 0 or total_need <= 0:
		return allocations

	if policy == "Sequential by Company":
		pool = [dict(p) for p in pool]
		for t in targets:
			need = t["need"]
			for p in pool:
				if need <= 0.005:
					break
				take = flt(min(need, p["amount"]), 2)
				if take <= 0:
					continue
				allocations.append(
					{
						**t,
						"mode_of_payment": p["mode_of_payment"],
						"reference_no": p["reference_no"],
						"amount": take,
					}
				)
				p["amount"] = flt(p["amount"] - take, 2)
				need = flt(need - take, 2)
		return allocations

	# Proportional: every invoice receives the same mix of payment modes
	factor = min(1.0, total_pool / total_need)
	used = {i: flt(p["amount"] * min(1.0, total_need / total_pool), 2) for i, p in enumerate(pool)}
	given = {i: 0.0 for i in range(len(pool))}
	for ti, t in enumerate(targets):
		row_total = flt(t["need"] * factor, 2)
		last_target = ti == len(targets) - 1
		running = 0.0
		for pi, p in enumerate(pool):
			if last_target:
				amt = flt(used[pi] - given[pi], 2)
			elif pi == len(pool) - 1:
				amt = flt(row_total - running, 2)
			else:
				amt = flt(row_total * used[pi] / sum(used.values()), 2)
			amt = max(amt, 0)
			running += amt
			given[pi] += amt
			if amt > 0:
				allocations.append(
					{
						**t,
						"mode_of_payment": p["mode_of_payment"],
						"reference_no": p["reference_no"],
						"amount": amt,
					}
				)
	return allocations


def allocate_and_post(batch, payments: list[dict] | None, change_due: float | None):
	"""Assign the unallocated part of the batch's payments to submitted invoices, then post entries."""
	types = _mode_types()
	pool = []
	change_left = flt(change_due)
	for p in batch.payments:
		amount = flt(p.amount)
		if types.get(p.mode_of_payment) == "Cash" and change_left > 0:
			cut = min(amount, change_left)
			amount -= cut
			change_left -= cut
		already = sum(
			flt(a.amount)
			for a in batch.allocations
			if a.mode_of_payment == p.mode_of_payment
			and (a.reference_no or None) == (p.reference_no or None)
			and a.status != "Cancelled"
		)
		amount = flt(amount - already, 2)
		if amount > 0.005:
			pool.append(
				{"mode_of_payment": p.mode_of_payment, "reference_no": p.reference_no, "amount": amount}
			)

	targets = []
	for comp in batch.companies:
		if comp.status != "Submitted" or not comp.sales_invoice:
			continue
		assigned = sum(
			flt(a.amount)
			for a in batch.allocations
			if a.sales_invoice == comp.sales_invoice and a.status != "Cancelled"
		)
		need = flt(comp.grand_total) - assigned
		if need > 0.005:
			targets.append({"company": comp.company, "sales_invoice": comp.sales_invoice, "need": need})

	for a in _distribute(pool, targets, get_settings().payment_allocation_policy):
		batch.append(
			"allocations",
			{
				"company": a["company"],
				"sales_invoice": a["sales_invoice"],
				"mode_of_payment": a["mode_of_payment"],
				"reference_no": a["reference_no"],
				"amount": a["amount"],
				"status": "Pending",
			},
		)
	batch.flags.ignore_permissions = True
	batch.save()
	frappe.db.commit()  # nosemgrep

	for alloc in batch.allocations:
		if alloc.status not in ("Pending", "Failed"):
			continue
		frappe.db.savepoint("cc_payment")
		try:
			alloc.payment_entry = post_payment_entry(batch, alloc)
			alloc.status = "Posted"
			alloc.error = None
		except Exception as e:
			frappe.db.rollback(save_point="cc_payment")
			from car_cupid_service.services.billing_service import _clean_error

			alloc.status = "Failed"
			alloc.error = _clean_error(e)
			frappe.log_error(
				title=f"Car Cupid payment failed: {batch.name}",
				reference_doctype="Invoice Batch",
				reference_name=batch.name,
			)
		alloc.db_update()
		frappe.db.commit()  # nosemgrep


def post_payment_entry(batch, alloc) -> str:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	si = frappe.get_doc("Sales Invoice", alloc.sales_invoice)
	if si.docstatus != 1:
		frappe.throw(_("Invoice {0} is not submitted").format(si.name))
	amount = min(flt(alloc.amount), flt(si.outstanding_amount))
	if amount <= 0:
		frappe.throw(_("Invoice {0} has no outstanding amount").format(si.name))
	account = get_bank_cash_account(alloc.mode_of_payment, si.company)["account"]

	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Receive"
	pe.company = si.company
	pe.posting_date = nowdate()
	pe.mode_of_payment = alloc.mode_of_payment
	pe.party_type = "Customer"
	pe.party = si.customer
	pe.paid_from = si.debit_to
	pe.paid_to = account
	pe.paid_amount = amount
	pe.received_amount = amount
	pe.source_exchange_rate = 1
	pe.target_exchange_rate = 1
	pe.reference_no = alloc.reference_no or batch.name
	pe.reference_date = nowdate()
	pe.cc_invoice_batch = batch.name
	pe.cc_counter_session = batch.counter_session
	pe.remarks = _("Car Cupid {0}: {1} received against {2}").format(
		batch.name, alloc.mode_of_payment, si.name
	)
	if si.get("cost_center"):
		pe.cost_center = si.cost_center
	pe.append(
		"references",
		{
			"reference_doctype": "Sales Invoice",
			"reference_name": si.name,
			"allocated_amount": amount,
		},
	)
	pe.setup_party_account_field()
	pe.set_missing_values()
	pe.flags.ignore_permissions = True
	pe.insert()
	pe.submit()
	return pe.name
