"""Server-authoritative validation and defaults for sale lines.

Used by both Service Job Card Item and Counter Cash Memo Item so that job cards and the direct POS
follow exactly the same company-routing, pricing, discount and warehouse rules.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from car_cupid_service.services.settings import (
	get_company_map,
	get_company_row,
	get_default_company,
	get_settings,
	is_supervisor,
	max_discount,
)

LOCKED_AFTER_BILLING = ("item_code", "company", "price_list_rate", "discount_percentage", "uom")


# ----------------------------------------------------------------------------- lookups
def get_user_sales_person(user: str | None = None) -> str | None:
	user = user or frappe.session.user
	employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")
	if not employee:
		return None
	return frappe.db.get_value("Sales Person", {"employee": employee, "enabled": 1}, "name")


def default_item_type(item: dict) -> str:
	if cint(item.get("is_stock_item")):
		return "Part"
	group = (item.get("item_group") or "").lower()
	if "labour" in group or "labor" in group:
		return "Labour"
	if "consum" in group:
		return "Consumable"
	if "accessor" in group:
		return "Accessory"
	return "Service"


def route_company(item_code: str) -> str | None:
	"""Pick the company for an item: the only enabled company with an Item Default row, else default."""
	companies = get_company_map()
	settings = get_settings()
	if cint(settings.auto_route_company_from_item):
		item_companies = frappe.get_all(
			"Item Default", filters={"parent": item_code, "parenttype": "Item"}, pluck="company"
		)
		enabled = [c for c in item_companies if c in companies]
		if len(enabled) == 1:
			return enabled[0]
	return get_default_company()


def get_price(item_code: str, company: str, customer: str | None = None, uom: str | None = None) -> float:
	"""Selling price from the company's configured price list.

	Pricing Rules are intentionally not applied here; invoices are created with ignore_pricing_rule so
	the price the customer saw at the counter is exactly the price that is invoiced.
	"""
	row = get_company_row(company, throw=False)
	price_list = (row and row.selling_price_list) or frappe.db.get_single_value(
		"Selling Settings", "selling_price_list"
	)
	if not price_list:
		return 0.0
	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	uom = uom or stock_uom
	today = getdate(nowdate())

	prices = frappe.get_all(
		"Item Price",
		filters={"item_code": item_code, "price_list": price_list, "uom": ("in", list({uom, stock_uom}))},
		fields=["price_list_rate", "uom", "customer", "valid_from", "valid_upto"],
		order_by="customer desc, valid_from desc",
	)
	best = None
	for p in prices:
		if p.valid_from and getdate(p.valid_from) > today:
			continue
		if p.valid_upto and getdate(p.valid_upto) < today:
			continue
		if p.customer and p.customer != customer:
			continue
		score = (1 if p.customer else 0, 1 if p.uom == uom else 0)
		if best is None or score > best[0]:
			best = (score, p)
	if not best:
		return 0.0
	price = best[1]
	rate = flt(price.price_list_rate)
	if price.uom != uom:
		factor = (
			flt(
				frappe.db.get_value(
					"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
				)
			)
			or 1
		)
		rate = rate * factor
	return rate


def get_stock_qty(item_code: str, warehouse: str | None) -> float:
	if not warehouse:
		return 0.0
	return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))


def get_line_defaults(item_code: str, company: str | None = None, customer: str | None = None) -> dict:
	item = frappe.db.get_value(
		"Item",
		item_code,
		[
			"item_name",
			"is_stock_item",
			"stock_uom",
			"sales_uom",
			"item_group",
			"disabled",
			"is_sales_item",
			"has_variants",
		],
		as_dict=True,
	)
	if not item:
		frappe.throw(_("Item {0} not found").format(item_code))
	company = company or route_company(item_code)
	row = get_company_row(company, throw=False) if company else None
	uom = item.sales_uom or item.stock_uom
	warehouse = row.default_warehouse if (row and cint(item.is_stock_item)) else None
	rate = get_price(item_code, company, customer, uom) if company else 0
	return {
		"item_code": item_code,
		"item_name": item.item_name,
		"item_type": default_item_type(item),
		"is_stock_item": cint(item.is_stock_item),
		"company": company,
		"company_label": (row and (row.short_label or row.company)) or company,
		"company_color": (row and row.color) or None,
		"uom": uom,
		"warehouse": warehouse,
		"price_list_rate": rate,
		"standard_rate": rate,
		"actual_qty": get_stock_qty(item_code, warehouse) if warehouse else None,
	}


# ----------------------------------------------------------------------------- validation
def validate_lines(doc, table: str = "items", is_job_card: bool = False):
	settings = get_settings()
	companies = get_company_map()
	if not companies:
		frappe.throw(
			_(
				"No companies are enabled in Car Cupid Settings. Ask your System Manager to configure them first."
			),
			title=_("Setup Incomplete"),
		)
	supervisor = is_supervisor()
	before = doc.get_doc_before_save()
	before_rows = {r.name: r for r in (before.get(table) if before else [])}
	user_sp = None
	errors = []

	for row in doc.get(table):
		label = _("Row {0} ({1})").format(row.idx, row.item_code or "")
		item = frappe.db.get_value(
			"Item",
			row.item_code,
			[
				"item_name",
				"is_stock_item",
				"stock_uom",
				"sales_uom",
				"item_group",
				"disabled",
				"is_sales_item",
				"has_variants",
			],
			as_dict=True,
		)
		if not item:
			errors.append(_("{0}: item does not exist").format(label))
			continue
		if cint(item.disabled):
			errors.append(_("{0}: item is disabled").format(label))
		if not cint(item.is_sales_item):
			errors.append(_("{0}: item is not marked as a sales item").format(label))
		if cint(item.has_variants):
			errors.append(_("{0}: pick a specific variant, not the template item").format(label))

		row.item_name = row.item_name or item.item_name
		row.is_stock_item = cint(item.is_stock_item)
		row.item_type = row.item_type or default_item_type(item)
		row.uom = row.uom or item.sales_uom or item.stock_uom
		if flt(row.qty) <= 0:
			errors.append(_("{0}: quantity must be greater than zero").format(label))

		# ---- billed lines are frozen
		prev = before_rows.get(row.name)
		if prev and flt(prev.billed_qty) > 0:
			for field in LOCKED_AFTER_BILLING:
				if str(prev.get(field) or "") != str(row.get(field) or ""):
					errors.append(
						_(
							"{0}: {1} cannot be changed after the line is invoiced. Cancel/return the invoice instead."
						).format(label, frappe.unscrub(field))
					)
		if flt(row.qty) < flt(row.billed_qty):
			errors.append(
				_("{0}: quantity cannot be less than the already invoiced quantity {1}").format(
					label, row.billed_qty
				)
			)
		if is_job_card:
			net_issued = flt(row.issued_qty) - flt(row.returned_qty)
			if flt(row.qty) < net_issued:
				errors.append(
					_(
						"{0}: {1} already issued from stores; return the extra material before reducing quantity"
					).format(label, net_issued)
				)

		# ---- company routing
		if not row.company:
			row.company = route_company(row.item_code)
		if row.company not in companies:
			errors.append(_("{0}: company {1} is not enabled for Car Cupid").format(label, row.company))
			continue
		if prev and prev.company and prev.company != row.company:
			if not cint(settings.allow_company_override) and not supervisor:
				errors.append(
					_("{0}: only a Billing Supervisor can change the company on a line").format(label)
				)
			elif flt(row.issued_qty) - flt(row.returned_qty) > 0:
				errors.append(
					_("{0}: return issued material before moving the line to another company").format(label)
				)
			else:
				row.warehouse = None
				doc.add_comment(
					"Info",
					_("Line {0} {1}: company changed from {2} to {3}").format(
						row.idx, row.item_code, prev.company, row.company
					),
				)
		if cint(settings.restrict_items_to_company_defaults) and not frappe.db.exists(
			"Item Default", {"parent": row.item_code, "company": row.company}
		):
			errors.append(
				_("{0}: item is not configured (Item Defaults) for company {1}").format(label, row.company)
			)

		company_row = companies[row.company]
		if row.is_stock_item:
			row.warehouse = row.warehouse or company_row.default_warehouse
			if not row.warehouse:
				errors.append(
					_("{0}: set a Store Warehouse for {1} in Car Cupid Settings").format(label, row.company)
				)
			elif frappe.db.get_value("Warehouse", row.warehouse, "company") != row.company:
				errors.append(
					_("{0}: warehouse {1} does not belong to {2}").format(label, row.warehouse, row.company)
				)
		else:
			row.warehouse = None

		# ---- salesperson
		if not row.sales_person:
			if doc.meta.has_field("default_sales_person") and doc.get("default_sales_person"):
				row.sales_person = doc.default_sales_person
			else:
				if user_sp is None:
					user_sp = get_user_sales_person() or ""
				row.sales_person = user_sp or None

		# ---- pricing (server side; client values are never trusted)
		if not (prev and flt(prev.billed_qty) > 0):
			standard = get_price(row.item_code, row.company, doc.get("customer"), row.uom)
			row.standard_rate = standard
			if not flt(row.price_list_rate):
				row.price_list_rate = standard
			if flt(row.price_list_rate) != flt(standard) and not supervisor:
				if prev and flt(prev.price_list_rate) == flt(row.price_list_rate):
					pass  # unchanged value approved earlier by a supervisor
				else:
					errors.append(
						_(
							"{0}: rate {1} differs from the price list rate {2}. Only a Billing Supervisor can override prices."
						).format(label, row.price_list_rate, standard)
					)
			if not flt(row.price_list_rate) and not supervisor:
				errors.append(_("{0}: no selling price found for company {1}").format(label, row.company))

		# ---- discount approval
		disc = flt(row.discount_percentage)
		if disc < 0 or disc > 100:
			errors.append(_("{0}: discount must be between 0 and 100%").format(label))
		if disc > max_discount():
			if supervisor:
				if not row.discount_approved_by or (prev and flt(prev.discount_percentage) != disc):
					row.discount_approved_by = frappe.session.user
			elif not (prev and flt(prev.discount_percentage) == disc and prev.discount_approved_by):
				errors.append(
					_("{0}: discount {1}% exceeds {2}%. Ask a Billing Supervisor to approve it.").format(
						label, disc, max_discount()
					)
				)
		elif row.discount_approved_by and disc <= max_discount():
			row.discount_approved_by = None

		row.rate = flt(flt(row.price_list_rate) * (1 - disc / 100.0), row.precision("rate"))
		row.amount = flt(row.rate * flt(row.qty), row.precision("amount"))

	if errors:
		frappe.throw("<br>".join(errors), title=_("Please correct these lines"))


def set_totals(doc, table: str = "items"):
	rows = doc.get(table)
	doc.total_qty = sum(flt(r.qty) for r in rows)
	doc.net_total = sum(flt(r.amount) for r in rows)


def company_summary(doc, table: str = "items") -> list[dict]:
	companies = get_company_map()
	out = {}
	for r in doc.get(table):
		c = out.setdefault(
			r.company,
			{
				"company": r.company,
				"label": (companies.get(r.company) or {}).get("short_label") or r.company,
				"color": (companies.get(r.company) or {}).get("color"),
				"lines": 0,
				"net_total": 0.0,
				"billed_amount": 0.0,
			},
		)
		c["lines"] += 1
		c["net_total"] += flt(r.amount)
		c["billed_amount"] += flt(r.rate) * flt(r.billed_qty)
	return list(out.values())
