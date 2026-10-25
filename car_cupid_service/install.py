"""Install / migrate hooks: roles, custom fields on standard documents, role access to masters."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property

CC_ROLES = [
	"Counter Salesperson",
	"Service Advisor",
	"Service Engineer",
	"Storekeeper",
	"Cashier",
	"Billing Supervisor",
	"Incentive Manager",
]
# Accounts Manager and System Manager ship with ERPNext / Frappe.
ALL_CC_ROLES = [*CC_ROLES, "Accounts Manager"]

# (doctype, roles that may read, roles that may also create+write)
MASTER_ACCESS = [
	("Customer", ALL_CC_ROLES, ["Counter Salesperson", "Service Advisor", "Cashier", "Billing Supervisor"]),
	("Contact", ALL_CC_ROLES, ["Counter Salesperson", "Service Advisor", "Cashier", "Billing Supervisor"]),
	("Address", ALL_CC_ROLES, ["Counter Salesperson", "Service Advisor", "Cashier", "Billing Supervisor"]),
	("Customer Group", ALL_CC_ROLES, []),
	("Territory", ALL_CC_ROLES, []),
	("Item", ALL_CC_ROLES, []),
	("Item Group", ALL_CC_ROLES, []),
	("Item Price", ALL_CC_ROLES, []),
	("Price List", ALL_CC_ROLES, []),
	("UOM", ALL_CC_ROLES, []),
	("Bin", ALL_CC_ROLES, []),
	("Warehouse", ALL_CC_ROLES, []),
	("Company", ALL_CC_ROLES, []),
	("Sales Person", ALL_CC_ROLES, []),
	("Employee", ALL_CC_ROLES, []),
	("Mode of Payment", ALL_CC_ROLES, []),
	("Sales Taxes and Charges Template", ALL_CC_ROLES, []),
	("Item Tax Template", ALL_CC_ROLES, []),
	("Sales Invoice", ALL_CC_ROLES, []),
	("Payment Entry", ["Cashier", "Billing Supervisor", "Accounts Manager"], []),
	("Material Request", ["Storekeeper", "Service Engineer", "Service Advisor", "Billing Supervisor"], []),
	("Stock Entry", ["Storekeeper", "Service Engineer", "Billing Supervisor"], []),
]


def get_custom_fields():
	ro = {"read_only": 1, "no_copy": 1}
	return {
		"Sales Invoice": [
			{
				"fieldname": "cc_section",
				"fieldtype": "Section Break",
				"label": "Car Cupid",
				"insert_after": "remarks",
				"collapsible": 1,
			},
			{
				"fieldname": "cc_invoice_batch",
				"fieldtype": "Link",
				"label": "Invoice Batch",
				"options": "Invoice Batch",
				"insert_after": "cc_section",
				"search_index": 1,
				**ro,
			},
			{
				"fieldname": "cc_source_doctype",
				"fieldtype": "Link",
				"label": "Source Type",
				"options": "DocType",
				"insert_after": "cc_invoice_batch",
				**ro,
			},
			{
				"fieldname": "cc_source_name",
				"fieldtype": "Dynamic Link",
				"label": "Source Document",
				"options": "cc_source_doctype",
				"insert_after": "cc_source_doctype",
				"search_index": 1,
				**ro,
			},
			{
				"fieldname": "cc_col",
				"fieldtype": "Column Break",
				"insert_after": "cc_source_name",
			},
			{
				"fieldname": "cc_vehicle",
				"fieldtype": "Link",
				"label": "Vehicle",
				"options": "Customer Vehicle",
				"insert_after": "cc_col",
				**ro,
			},
			{
				"fieldname": "cc_counter_session",
				"fieldtype": "Link",
				"label": "Counter Session",
				"options": "Counter Session",
				"insert_after": "cc_vehicle",
				**ro,
			},
			{
				"fieldname": "cc_discount_rows",
				"fieldtype": "Small Text",
				"label": "Car Cupid Bill Discount Rows",
				"insert_after": "cc_counter_session",
				"hidden": 1,
				**ro,
			},
		],
		"Sales Invoice Item": [
			{
				"fieldname": "cc_sales_person",
				"fieldtype": "Link",
				"label": "Car Cupid Salesperson",
				"options": "Sales Person",
				"insert_after": "item_name",
				"no_copy": 0,
			},
			{
				"fieldname": "cc_item_type",
				"fieldtype": "Data",
				"label": "Car Cupid Line Type",
				"insert_after": "cc_sales_person",
				"read_only": 1,
			},
			{
				"fieldname": "cc_source_row",
				"fieldtype": "Data",
				"label": "Car Cupid Source Row",
				"insert_after": "cc_item_type",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 0,
			},
		],
		"Payment Entry": [
			{
				"fieldname": "cc_invoice_batch",
				"fieldtype": "Link",
				"label": "Car Cupid Invoice Batch",
				"options": "Invoice Batch",
				"insert_after": "reference_date",
				**ro,
			},
			{
				"fieldname": "cc_counter_session",
				"fieldtype": "Link",
				"label": "Counter Session",
				"options": "Counter Session",
				"insert_after": "cc_invoice_batch",
				"search_index": 1,
				**ro,
			},
		],
		"Stock Entry": [
			{
				"fieldname": "cc_material_issue_request",
				"fieldtype": "Link",
				"label": "Material Issue Request",
				"options": "Material Issue Request",
				"insert_after": "remarks",
				**ro,
			},
			{
				"fieldname": "cc_job_card",
				"fieldtype": "Link",
				"label": "Service Job Card",
				"options": "Service Job Card",
				"insert_after": "cc_material_issue_request",
				**ro,
			},
		],
		"Material Request": [
			{
				"fieldname": "cc_job_card",
				"fieldtype": "Link",
				"label": "Service Job Card",
				"options": "Service Job Card",
				"insert_after": "material_request_type",
				"search_index": 1,
				**ro,
			},
		],
	}


def create_roles():
	for role in CC_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def grant_master_access():
	"""Give Car Cupid roles the read/create rights they need on ERPNext masters.

	`add_permission` copies the standard permissions into Custom DocPerm before adding a row, so the
	existing ERPNext roles keep their access.
	"""
	for doctype, read_roles, write_roles in MASTER_ACCESS:
		if not frappe.db.exists("DocType", doctype):
			continue
		for role in read_roles:
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
				add_permission(doctype, role, 0)
			update_permission_property(doctype, role, 0, "read", 1, validate=False)
			update_permission_property(doctype, role, 0, "print", 1, validate=False)
			update_permission_property(doctype, role, 0, "report", 1, validate=False)
		for role in write_roles:
			update_permission_property(doctype, role, 0, "write", 1, validate=False)
			update_permission_property(doctype, role, 0, "create", 1, validate=False)


def set_default_settings():
	settings = frappe.get_single("Car Cupid Settings")
	changed = False
	if not settings.cash_mode_of_payment and frappe.db.exists("Mode of Payment", "Cash"):
		settings.cash_mode_of_payment = "Cash"
		changed = True
	if not settings.denominations:
		settings.denominations = "500,200,100,50,20,10,5,2,1"
		changed = True
	if changed:
		settings.flags.ignore_mandatory = True
		settings.flags.ignore_validate = True
		settings.flags.ignore_links = True
		settings.save(ignore_permissions=True)


def setup():
	create_roles()
	create_custom_fields(get_custom_fields(), ignore_validate=True, update=True)
	grant_master_access()


def before_install():
	create_roles()


def after_install():
	setup()
	set_default_settings()
	frappe.db.commit()


def after_migrate():
	setup()


def before_uninstall():
	for doctype, fields in get_custom_fields().items():
		for df in fields:
			name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": df["fieldname"]})
			if name:
				frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
