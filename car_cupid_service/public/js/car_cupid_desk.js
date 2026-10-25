// Car Cupid desk helpers shared by the forms
frappe.provide("car_cupid");

car_cupid.uuid = () =>
	(crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

car_cupid.company_summary_html = (rows) => {
	if (!rows || !rows.length) return "";
	const cards = rows
		.map((r) => {
			const dot = r.color ? `<span style="width:8px;height:8px;border-radius:50%;background:${r.color};display:inline-block"></span>` : "";
			return `<div class="cc-card">
				<div class="lbl">${dot} ${frappe.utils.escape_html(r.label || r.company)}</div>
				<div class="val">${format_currency(r.net_total)}</div>
				<div class="sub">${r.lines} ${__("line(s)")} · ${__("billed")} ${format_currency(r.billed_amount || 0)}</div>
			</div>`;
		})
		.join("");
	return `<div class="cc-summary">${cards}</div>`;
};

car_cupid.render_company_summary = (frm) => {
	const rows = {};
	(frm.doc.items || []).forEach((r) => {
		const c = (rows[r.company] = rows[r.company] || { company: r.company, label: r.company, lines: 0, net_total: 0, billed_amount: 0 });
		c.lines += 1;
		c.net_total += flt(r.amount);
		c.billed_amount += flt(r.rate) * flt(r.billed_qty);
	});
	(frm.doc.bill_discounts || []).forEach((d) => {
		if (rows[d.company]) rows[d.company].net_total -= flt(d.discount_amount);
	});
	frm.get_field("company_summary").$wrapper.html(car_cupid.company_summary_html(Object.values(rows)));
};

// limit the bill-discount company to companies that have lines on the document
car_cupid.setup_bill_discount_query = (frm) => {
	frm.set_query("company", "bill_discounts", () => ({
		filters: { name: ["in", [...new Set((frm.doc.items || []).map((r) => r.company).filter(Boolean))]] },
	}));
};

car_cupid.open_pos = (route_options) => {
	frappe.route_options = route_options || {};
	frappe.set_route("car-cupid-pos");
};
