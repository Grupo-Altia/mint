frappe.listview_settings['Payment Entry'] = {
	add_fields: ["clearance_date"],
	get_indicator: function (doc) {
		if (doc.docstatus === 2) {
			return [__("Cancelado"), "red", "docstatus,=,2"];
		} else if (doc.docstatus === 0) {
			return [__("Por conciliar"), "orange", "docstatus,=,0"];
		} else if (doc.docstatus === 1) {
			if (doc.clearance_date) {
				return [__("Conciliado"), "green", "docstatus,=,1|clearance_date,!=,"];
			} else {
				return [__("Por conciliar"), "orange", "docstatus,=,1|clearance_date,=,"];
			}
		}
	}
};
