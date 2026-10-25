"""Test fixtures: three legal companies, warehouses, items, prices, payment modes and settings.

Usable on any ERPNext v16 site with the setup wizard completed:
    bench --site <site> execute car_cupid_service.tests.utils.setup_test_world
"""

import frappe
from frappe.utils import nowdate

COMPANIES = [
	("Car Cupid Accessories", "CCA", "#E91E63"),
	("Car Cupid Detailing", "CCD", "#3F51B5"),
	("Car Cupid Care", "CCC", "#009688"),
]
ITEMS = [
	# code, name, group, is_stock, company_abbr, price
	("CC-SEATCOVER", "Premium Seat Cover Set", "Products", 1, "CCA", 4500),
	("CC-CERAMIC", "Ceramic Coating Service", "Services", 0, "CCD", 12000),
	("CC-LABOUR", "Fitting Labour", "Services", 0, "CCD", 500),
	("CC-MATS", "7D Floor Mats", "Products", 1, "CCC", 2500),
]
CUSTOMER = "CC Test Owner"
VEHICLE = "MH12AB1234"
SALES_PERSON = "CC Test Salesperson"


def _company(name, abbr):
	if not frappe.db.exists("Company", name):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": name,
				"abbr": abbr,
				"default_currency": "INR",
				"country": "India",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)
	return name


def _warehouse(name, company):
	abbr = frappe.db.get_value("Company", company, "abbr")
	full = f"{name} - {abbr}"
	if not frappe.db.exists("Warehouse", full):
		parent = frappe.db.get_value("Warehouse", {"company": company, "is_group": 1}, "name")
		frappe.get_doc(
			{"doctype": "Warehouse", "warehouse_name": name, "company": company, "parent_warehouse": parent}
		).insert(ignore_permissions=True)
	return full


def _bank_account(company):
	abbr = frappe.db.get_value("Company", company, "abbr")
	name = f"UPI Collections - {abbr}"
	if not frappe.db.exists("Account", name):
		parent = frappe.db.get_value("Account", {"company": company, "account_type": "Bank", "is_group": 1}, "name")
		parent = parent or frappe.db.get_value("Account", {"company": company, "account_name": "Bank Accounts"}, "name")
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "UPI Collections",
				"company": company,
				"parent_account": parent,
				"account_type": "Bank",
			}
		).insert(ignore_permissions=True)
	return name


def _mode(name, mtype, account_for):
	mop = frappe.get_doc("Mode of Payment", name) if frappe.db.exists("Mode of Payment", name) else frappe.get_doc(
		{"doctype": "Mode of Payment", "mode_of_payment": name, "type": mtype, "enabled": 1}
	)
	have = {a.company for a in mop.get("accounts")}
	for company, _abbr, _c in COMPANIES:
		if company not in have:
			mop.append("accounts", {"company": company, "default_account": account_for(company)})
	mop.enabled = 1
	mop.save(ignore_permissions=True) if not mop.is_new() else mop.insert(ignore_permissions=True)


def setup_test_world():
	frappe.set_user("Administrator")
	for name, abbr, _c in COMPANIES:
		_company(name, abbr)

	_mode("Cash", "Cash", lambda c: frappe.db.get_value("Company", c, "default_cash_account"))
	_mode("UPI", "Bank", _bank_account)

	price_list = "Standard Selling"
	if not frappe.db.exists("Price List", price_list):
		frappe.get_doc({"doctype": "Price List", "price_list_name": price_list, "selling": 1, "currency": "INR"}).insert()

	for group in ("Products", "Services"):
		if not frappe.db.exists("Item Group", group):
			frappe.get_doc({"doctype": "Item Group", "item_group_name": group, "parent_item_group": "All Item Groups"}).insert()

	settings = frappe.get_single("Car Cupid Settings")
	settings.set("companies", [])
	for name, abbr, color in COMPANIES:
		cost_center = frappe.db.get_value("Company", name, "cost_center")
		settings.append(
			"companies",
			{
				"enabled": 1,
				"company": name,
				"short_label": abbr,
				"color": color,
				"default_warehouse": _warehouse("Stores", name),
				"service_bay_warehouse": _warehouse("Service Bay", name),
				"selling_price_list": price_list,
				"cost_center": cost_center,
			},
		)
	settings.default_company = COMPANIES[0][0]
	settings.cash_mode_of_payment = "Cash"
	settings.material_issue_policy = "Issue Required for Stock Items"
	settings.require_work_completion = 1
	settings.allow_partial_billing = 0
	settings.auto_create_material_request = 0
	settings.max_discount_without_approval = 10
	settings.allow_credit_sale = 1
	settings.cash_variance_tolerance = 50
	settings.require_open_session_for_billing = 1
	settings.vehicle_number_format = "Indian Registration"
	settings.save(ignore_permissions=True)

	abbr_to_company = {abbr: name for name, abbr, _c in COMPANIES}
	for code, item_name, group, is_stock, abbr, price in ITEMS:
		company = abbr_to_company[abbr]
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": item_name,
					"item_group": group,
					"stock_uom": "Nos",
					"is_stock_item": is_stock,
					"include_item_in_manufacturing": 0,
					"valuation_rate": price * 0.5 if is_stock else 0,
					"item_defaults": [{"company": company, "default_warehouse": _warehouse("Stores", company) if is_stock else None}],
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("Item Price", {"item_code": code, "price_list": price_list}):
			frappe.get_doc(
				{"doctype": "Item Price", "item_code": code, "price_list": price_list, "price_list_rate": price}
			).insert(ignore_permissions=True)

	if not frappe.db.exists("Customer", CUSTOMER):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": CUSTOMER,
				"customer_type": "Individual",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Customer Vehicle", VEHICLE):
		frappe.get_doc(
			{"doctype": "Customer Vehicle", "vehicle_number": "mh 12 ab 1234", "customer": CUSTOMER, "make": "Hyundai", "model": "Creta"}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Sales Person", SALES_PERSON):
		frappe.get_doc(
			{"doctype": "Sales Person", "sales_person_name": SALES_PERSON, "parent_sales_person": "Sales Team", "enabled": 1}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Service Incentive Rule", "CC Test 2 Percent"):
		frappe.get_doc(
			{"doctype": "Service Incentive Rule", "rule_name": "CC Test 2 Percent", "basis": "Percentage of Net Sales", "rate": 2}
		).insert(ignore_permissions=True)
	frappe.db.commit()


def add_stock(item_code: str, company: str, qty: float, warehouse_name: str = "Stores"):
	wh = _warehouse(warehouse_name, company)
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"purpose": "Material Receipt",
			"company": company,
			"posting_date": nowdate(),
			"items": [{"item_code": item_code, "qty": qty, "t_warehouse": wh, "basic_rate": 1000}],
		}
	)
	se.insert(ignore_permissions=True)
	se.submit()
	return se.name


def bin_qty(item_code: str, warehouse: str) -> float:
	return frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0.0


def open_session(opening_float=1000):
	from car_cupid_service.api.counter import open_session as _open

	return _open("Test Counter", opening_float)["name"]
