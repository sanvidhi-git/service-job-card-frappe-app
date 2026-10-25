# Copyright (c) 2026, Sanvidhi Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

from car_cupid_service.services.settings import denominations, get_settings, is_supervisor

OUTFLOW_TYPES = ("Cash Out", "Refund", "Bank Deposit")


class CounterSession(Document):
	def before_insert(self):
		self.status = "Open"
		self.cashier = self.cashier or frappe.session.user
		if not self.closing_denominations:
			for d in denominations():
				self.append("closing_denominations", {"denomination": d, "count": 0})

	def validate(self):
		self.validate_single_open_session()
		self.stamp_movements()
		self.compute_summary()
		self.compute_closing()

	def before_submit(self):
		self.compute_summary()
		self.compute_closing()
		if not self.closing_denominations and not flt(self.physical_cash) and flt(self.expected_cash):
			frappe.throw(_("Count the cash in the drawer before closing the session."))
		tolerance = flt(get_settings().cash_variance_tolerance)
		if abs(flt(self.variance)) > tolerance:
			if not self.variance_reason:
				frappe.throw(
					_("Cash variance of {0} is above the tolerance of {1}. Enter a reason.").format(
						frappe.format_value(self.variance, "Currency"), frappe.format_value(tolerance, "Currency")
					),
					title=_("Variance Needs Explanation"),
				)
			if not cint(self.variance_approved):
				frappe.throw(
					_("Cash variance of {0} needs approval from a Billing Supervisor or Accounts Manager before closing.").format(
						frappe.format_value(self.variance, "Currency")
					),
					title=_("Approval Required"),
				)

	def on_submit(self):
		self.db_set({"status": "Closed", "closing_time": now_datetime()})

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	# ------------------------------------------------------------------
	def validate_single_open_session(self):
		if self.docstatus != 0 or not cint(get_settings().one_open_session_per_user):
			return
		other = frappe.db.get_value(
			"Counter Session",
			{"cashier": self.cashier, "docstatus": 0, "name": ("!=", self.name or "")},
			"name",
		)
		if other:
			frappe.throw(
				_("Cashier {0} already has an open counter session {1}. Close it first.").format(self.cashier, other),
				title=_("Session Already Open"),
			)

	def stamp_movements(self):
		supervisor = is_supervisor()
		for m in self.movements:
			if flt(m.amount) <= 0:
				frappe.throw(_("Row {0}: amount must be positive").format(m.idx))
			if not m.posted_by:
				m.posted_by = frappe.session.user
				m.posted_on = now_datetime()
				if supervisor:
					m.approved_by = frappe.session.user
		before = self.get_doc_before_save()
		if before:
			old = {m.name: m for m in before.movements}
			for m in self.movements:
				prev = old.get(m.name)
				if prev and (flt(prev.amount) != flt(m.amount) or prev.movement_type != m.movement_type) and not supervisor:
					frappe.throw(_("Row {0}: only a supervisor can edit a recorded cash movement").format(m.idx))
			removed = set(old) - {m.name for m in self.movements}
			if removed and not supervisor:
				frappe.throw(_("Only a supervisor can delete a recorded cash movement"))
		if cint(self.variance_approved) and not self.variance_approved_by:
			self.variance_approved_by = frappe.session.user

	def compute_summary(self):
		cash_mode_types = dict(frappe.get_all("Mode of Payment", fields=["name", "type"], as_list=True))
		rows = []
		if not self.is_new():
			rows = frappe.db.sql(
				"""
				select mode_of_payment, count(*) as transactions,
					sum(case when payment_type = 'Receive' then paid_amount else -paid_amount end) as amount
				from `tabPayment Entry`
				where docstatus = 1 and cc_counter_session = %s
				group by mode_of_payment
				""",
				self.name,
				as_dict=True,
			)
		self.set("mode_summary", [])
		cash = non_cash = 0.0
		for r in rows:
			is_cash = 1 if cash_mode_types.get(r.mode_of_payment) == "Cash" else 0
			self.append(
				"mode_summary",
				{"mode_of_payment": r.mode_of_payment, "is_cash": is_cash, "transactions": r.transactions, "amount": r.amount},
			)
			if is_cash:
				cash += flt(r.amount)
			else:
				non_cash += flt(r.amount)
		self.cash_receipts = cash
		self.non_cash_receipts = non_cash
		self.cash_in = sum(flt(m.amount) for m in self.movements if m.movement_type == "Cash In")
		self.cash_out = sum(flt(m.amount) for m in self.movements if m.movement_type in OUTFLOW_TYPES)
		# Expected physical cash = float + cash receipts + cash in - payouts/refunds/deposits
		self.expected_cash = flt(self.opening_float) + cash + flt(self.cash_in) - flt(self.cash_out)

	def compute_closing(self):
		counted = 0.0
		has_count = False
		for d in self.closing_denominations:
			d.amount = flt(d.denomination) * cint(d.count)
			counted += d.amount
			if cint(d.count):
				has_count = True
		if has_count:
			self.physical_cash = counted
		self.variance = flt(self.physical_cash) - flt(self.expected_cash)
