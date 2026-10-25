app_name = "car_cupid_service"
app_title = "Car Cupid Service"
app_publisher = "Sanvidhi Tech Pvt Ltd"
app_description = (
	"Multi-company service job cards, store fulfilment, counter cash and web POS for ERPNext v16"
)
app_email = "vijayshastri11@gmail.com"
app_license = "mit"
app_home = "/app/car-cupid-service"
app_logo_url = "/assets/car_cupid_service/images/car-cupid-logo.svg"

required_apps = ["erpnext"]

# Apps screen / desktop tile
# ------------------------------------------------------------------
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": app_logo_url,
		"title": "Car Cupid",
		"route": app_home,
		"has_permission": "car_cupid_service.api.permissions.has_app_permission",
		"sequence_id": 5,
	}
]

app_include_css = "/assets/car_cupid_service/css/car_cupid_desk.css"
app_include_js = "/assets/car_cupid_service/js/car_cupid_desk.js"

# Installation
# ------------------------------------------------------------------
before_install = "car_cupid_service.install.before_install"
after_install = "car_cupid_service.install.after_install"
after_migrate = "car_cupid_service.install.after_migrate"
before_uninstall = "car_cupid_service.install.before_uninstall"

# Document events on standard ERPNext documents
# ------------------------------------------------------------------
doc_events = {
	"Sales Invoice": {
		"on_submit": "car_cupid_service.services.invoice_hooks.on_sales_invoice_submit",
		"on_cancel": "car_cupid_service.services.invoice_hooks.on_sales_invoice_cancel",
	},
	"Payment Entry": {
		"on_cancel": "car_cupid_service.services.invoice_hooks.on_payment_entry_cancel",
	},
	"Stock Entry": {
		"on_cancel": "car_cupid_service.services.stock_service.on_stock_entry_cancel",
	},
}

# Row-level security: cashiers only see their own counter sessions
# ------------------------------------------------------------------
permission_query_conditions = {
	"Counter Session": "car_cupid_service.api.permissions.counter_session_query",
}
has_permission = {
	"Counter Session": "car_cupid_service.api.permissions.counter_session_has_permission",
}

# Desk list indicators
# ------------------------------------------------------------------
doctype_list_js = {}

# Scheduled housekeeping
# ------------------------------------------------------------------
scheduler_events = {
	"hourly": ["car_cupid_service.services.billing_service.recover_stuck_batches"],
}

export_python_type_annotations = True
