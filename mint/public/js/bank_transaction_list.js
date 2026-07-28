frappe.listview_settings['Bank Transaction'] = frappe.listview_settings['Bank Transaction'] || {};

let old_onload = frappe.listview_settings['Bank Transaction'].onload;

frappe.listview_settings['Bank Transaction'].onload = function(listview) {
    if (old_onload) {
        old_onload(listview);
    }
};
