import sys

with open('/home/praveen/frappe-bench/apps/bench_manager/bench_manager/bench_manager/page/bench_dashboard/bench_dashboard.js', 'r') as f:
    content = f.read()

# Replace setup_app_actions
old_setup = """setup_app_actions() {
this.$container.find('#btn-new-app').on('click', () => this.show_new_app_dialog());
this.$container.find('#btn-get-app').on('click', () => this.show_get_app_dialog());
this.$container.find('#btn-refresh-apps').on('click', () => this.load_apps());
}

show_new_app_dialog() {
const self = this;
const d = new frappe.ui.Dialog({
title: 'Create New App',
fields: [
{ label: 'App Name', fieldname: 'app_name', fieldtype: 'Data', reqd: 1, description: 'snake_case, e.g., my_custom_app' },
{ label: 'App Title', fieldname: 'title', fieldtype: 'Data' },
{ label: 'Description', fieldname: 'description', fieldtype: 'Small Text' },
{ label: 'Publisher', fieldname: 'publisher', fieldtype: 'Data' },
{ label: 'Email', fieldname: 'email', fieldtype: 'Data' },
],
primary_action_label: 'Create App',
primary_action(values) {
d.hide();
self.append_console(`$ bench new-app --no-git ${values.app_name}`, 'command');
self.append_console(`Creating new Frappe app "${values.app_name}"...`, 'stdout');
self.show_live_activity(values.app_name);
frappe.call({
method: 'bench_manager.api.create_new_app',
args: values,
callback: (r) => {
if (r.message) {
frappe.show_alert({ message: r.message.message, indicator: 'blue' });
}
},
});
},
});
d.show();
}

show_get_app_dialog() {
const self = this;
const d = new frappe.ui.Dialog({
title: 'Get App from Git',
fields: [
{ label: 'Git URL', fieldname: 'git_url', fieldtype: 'Data', reqd: 1, description: 'HTTPS or SSH URL' },
{ label: 'Branch', fieldname: 'branch', fieldtype: 'Data', default: 'master' },
],
primary_action_label: 'Get App',
primary_action(values) {
d.hide();
self.append_console(`$ bench get-app ${values.git_url} --branch ${values.branch}`, 'command');
self.append_console(`Cloning repository and installing app. This may take a few minutes...`, 'stdout');
self.show_live_activity('get-app');
frappe.call({
method: 'bench_manager.api.get_app',
args: values,
callback: (r) => {
if (r.message) {
frappe.show_alert({ message: r.message.message, indicator: 'blue' });
}
},
});
},
});
d.show();
}"""

new_setup = """setup_app_actions() {
this.$container.find('#btn-new-app').on('click', () => this.show_new_app_dialog());
this.$container.find('#btn-refresh-apps').on('click', () => this.load_apps());

// Setup category filters
this.$container.find('.app-filter-chip').on('click', (e) => {
const $chip = $(e.currentTarget);
this.$container.find('.app-filter-chip').removeClass('active');
$chip.addClass('active');

const filter = $chip.data('filter');
this.apply_app_filter(filter);
});
}

apply_app_filter(filter) {
const $rows = this.$container.find('#apps-table-wrapper tbody tr');
if (filter === 'all') {
$rows.show();
return;
}

$rows.each(function() {
const source = $(this).data('source') || 'custom';
if (source === filter) {
$(this).show();
} else {
$(this).hide();
}
});
}

show_new_app_dialog() {
const self = this;
const d = new frappe.ui.Dialog({
title: 'Add New App',
fields: [
{
fieldtype: 'HTML',
fieldname: 'app_tabs',
options: `
<div class="new-app-dialog-tabs">
<button class="new-app-dialog-tab active" data-tab="custom_app">Custom App</button>
<button class="new-app-dialog-tab" data-tab="get_app">Get App</button>
<button class="new-app-dialog-tab" data-tab="frappe_store">Frappe Store</button>
</div>

<!-- Custom App Tab -->
<div class="new-app-tab-pane active" id="tab-custom_app">
<div class="form-group">
<label class="control-label">App Name</label>
<input type="text" class="input-with-feedback form-control" id="custom_app_name" placeholder="snake_case, e.g., my_custom_app">
</div>
<div class="form-group">
<label class="control-label">App Title</label>
<input type="text" class="input-with-feedback form-control" id="custom_app_title">
</div>
<div class="form-group">
<label class="control-label">Description</label>
<textarea class="input-with-feedback form-control" id="custom_app_desc" rows="2"></textarea>
</div>
<div class="row">
<div class="col-xs-6">
<div class="form-group">
<label class="control-label">Publisher</label>
<input type="text" class="input-with-feedback form-control" id="custom_app_publisher">
</div>
</div>
<div class="col-xs-6">
<div class="form-group">
<label class="control-label">Email</label>
<input type="email" class="input-with-feedback form-control" id="custom_app_email">
</div>
</div>
</div>
</div>

<!-- Get App Tab -->
<div class="new-app-tab-pane" id="tab-get_app">
<div class="form-group">
<label class="control-label">Git URL</label>
<input type="text" class="input-with-feedback form-control" id="get_app_url" placeholder="HTTPS or SSH URL">
</div>
<div class="form-group">
<label class="control-label">Branch</label>
<input type="text" class="input-with-feedback form-control" id="get_app_branch" value="master">
</div>
</div>

<!-- Frappe Store Tab -->
<div class="new-app-tab-pane" id="tab-frappe_store">
<div class="frappe-store-search-wrap">
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
<input type="text" class="frappe-store-search" placeholder="Search official apps...">
</div>
<div class="frappe-store-list">
<!-- Store items populated via JS -->
</div>
<div class="frappe-store-selected-count">
<span id="store-selected-text">0 apps selected</span>
<button class="btn btn-xs btn-default" id="store-clear-selection">Clear</button>
</div>
</div>
`
}
],
primary_action_label: 'Create App',
primary_action(values) {
const activeTab = d.$wrapper.find('.new-app-dialog-tab.active').data('tab');

if (activeTab === 'custom_app') {
const app_name = d.$wrapper.find('#custom_app_name').val();
if (!app_name) {
frappe.msgprint('App Name is required');
return;
}
d.hide();
self.append_console(`$ bench new-app --no-git ${app_name}`, 'command');
self.append_console(`Creating new Frappe app "${app_name}"...`, 'stdout');
self.show_live_activity(app_name);
frappe.call({
method: 'bench_manager.api.create_new_app',
args: {
app_name: app_name,
title: d.$wrapper.find('#custom_app_title').val(),
description: d.$wrapper.find('#custom_app_desc').val(),
publisher: d.$wrapper.find('#custom_app_publisher').val(),
email: d.$wrapper.find('#custom_app_email').val()
},
callback: (r) => {
if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
}
});
} else if (activeTab === 'get_app') {
const git_url = d.$wrapper.find('#get_app_url').val();
if (!git_url) {
frappe.msgprint('Git URL is required');
return;
}
const branch = d.$wrapper.find('#get_app_branch').val() || 'master';
d.hide();
self.append_console(`$ bench get-app ${git_url} --branch ${branch}`, 'command');
self.append_console(`Cloning repository and installing app. This may take a few minutes...`, 'stdout');
self.show_live_activity('get-app');
frappe.call({
method: 'bench_manager.api.get_app',
args: { git_url: git_url, branch: branch },
callback: (r) => {
if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
}
});
} else if (activeTab === 'frappe_store') {
const selectedApps = [];
d.$wrapper.find('.frappe-store-item.selected').each(function() {
selectedApps.push({
name: $(this).data('app-name'),
repo: $(this).data('app-repo')
});
});

if (selectedApps.length === 0) {
frappe.msgprint('Please select at least one app to install');
return;
}

d.hide();
// Handle multiple installations sequentially
let currentIndex = 0;

const installNextApp = () => {
if (currentIndex >= selectedApps.length) {
self.append_console('All selected Frappe Store apps have been processed.', 'success');
return;
}

const app = selectedApps[currentIndex];
self.append_console(`$ bench get-app ${app.repo}`, 'command');
self.append_console(`Fetching official app: ${app.name} (${currentIndex + 1}/${selectedApps.length})...`, 'stdout');
self.show_live_activity('frappe-store');

frappe.call({
method: 'bench_manager.api.get_app',
args: { git_url: app.repo, branch: '' },
callback: (r) => {
if (r.message) frappe.show_alert({ message: r.message.message, indicator: 'blue' });
// Ideally we would wait for the background job to finish, but for UI simplicity 
// we queue them up and let the backend process them via enqueue
currentIndex++;
setTimeout(installNextApp, 1000);
}
});
};

installNextApp();
}
}
});

// Setup tabs behavior
d.$wrapper.find('.new-app-dialog-tab').on('click', function() {
const tab = $(this).data('tab');
d.$wrapper.find('.new-app-dialog-tab').removeClass('active');
$(this).addClass('active');

d.$wrapper.find('.new-app-tab-pane').removeClass('active');
d.$wrapper.find(`#tab-${tab}`).addClass('active');

// Update primary button text
if (tab === 'custom_app') d.set_primary_action('Create App');
else if (tab === 'get_app') d.set_primary_action('Get App');
else if (tab === 'frappe_store') d.set_primary_action('Install Selected');
});

// Populate Frappe Store apps
const officialApps = [
{ id: 'erpnext', name: 'ERPNext', desc: 'Full-featured open-source ERP for accounting, inventory, manufacturing, HR, and CRM.', repo: 'https://github.com/frappe/erpnext.git', icon: 'E', color: '#2490ef' },
{ id: 'frappe_builder', name: 'Frappe Builder', desc: 'Visual no-code website builder with drag-and-drop blocks and dynamic data binding.', repo: 'https://github.com/frappe/builder.git', icon: 'B', color: '#1657A1' },
{ id: 'frappe_crm', name: 'Frappe CRM', desc: 'Modern, open-source CRM with deal pipeline, email, calls, notes, and AI integration.', repo: 'https://github.com/frappe/crm.git', icon: 'C', color: '#D923B2' },
{ id: 'frappe_drive', name: 'Frappe Drive', desc: 'File storage and document management with sharing, permissions, and collaborative editing.', repo: 'https://github.com/frappe/drive.git', icon: 'D', color: '#1A737D' },
{ id: 'ecommerce_integrations', name: 'eCommerce Integrations', desc: 'Connectors for Shopify, WooCommerce, and other eCommerce platforms with ERPNext.', repo: 'https://github.com/frappe/ecommerce_integrations.git', icon: 'E', color: '#6A90E2' },
{ id: 'frappe_hrms', name: 'Frappe HR', desc: 'Modern HR and Payroll management software.', repo: 'https://github.com/frappe/hrms.git', icon: 'H', color: '#F1604B' },
{ id: 'lms', name: 'Frappe LMS', desc: 'Easy-to-use Learning Management System.', repo: 'https://github.com/frappe/lms.git', icon: 'L', color: '#1EAC77' }
];

const $storeList = d.$wrapper.find('.frappe-store-list');
officialApps.forEach(app => {
const itemHtml = `
<div class="frappe-store-item" data-app-id="${app.id}" data-app-name="${app.name}" data-app-repo="${app.repo}">
<div class="frappe-store-item-checkbox">
<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3" style="display:none;"><polyline points="20 6 9 17 4 12"/></svg>
</div>
<div class="frappe-store-item-icon" style="background-color: ${app.color};">${app.icon}</div>
<div class="frappe-store-item-info">
<div class="frappe-store-item-name">${app.name}</div>
<div class="frappe-store-item-desc" title="${app.desc}">${app.desc}</div>
<div class="frappe-store-item-meta">Official App</div>
</div>
</div>
`;
$storeList.append(itemHtml);
});

// Setup selection
const updateSelectionCount = () => {
const count = d.$wrapper.find('.frappe-store-item.selected').length;
d.$wrapper.find('#store-selected-text').text(`${count} app${count !== 1 ? 's' : ''} selected`);
};

d.$wrapper.find('.frappe-store-item').on('click', function() {
$(this).toggleClass('selected');
if ($(this).hasClass('selected')) {
$(this).find('.frappe-store-item-checkbox svg').show();
} else {
$(this).find('.frappe-store-item-checkbox svg').hide();
}
updateSelectionCount();
});

d.$wrapper.find('#store-clear-selection').on('click', function(e) {
e.preventDefault();
d.$wrapper.find('.frappe-store-item').removeClass('selected');
d.$wrapper.find('.frappe-store-item-checkbox svg').hide();
updateSelectionCount();
});

// Setup search
d.$wrapper.find('.frappe-store-search').on('input', function() {
const term = $(this).val().toLowerCase();
d.$wrapper.find('.frappe-store-item').each(function() {
const name = $(this).data('app-name').toLowerCase();
const desc = $(this).find('.frappe-store-item-desc').text().toLowerCase();
if (name.includes(term) || desc.includes(term)) {
$(this).show();
} else {
$(this).hide();
}
});
});

d.show();
}"""

content = content.replace(old_setup, new_setup)

# Now let's update render_apps_table to add categories and badges
old_render_apps = """render_apps_table(apps) {
const $wrapper = this.$container.find('#apps-table-wrapper');

// Also load sites for the install dropdown
frappe.call({
method: 'bench_manager.api.list_sites',
callback: (r) => {
const sites = (r.message || []).map((s) => s.site_name);
let html = `<table class="bench-table">
<thead><tr>
<th>App Name</th><th>Git URL</th><th>Branch</th><th>Actions</th>
</tr></thead><tbody>`;

apps.forEach((app) => {
const gitUrl = app.git_url ? `<a href="${frappe.utils.escape_html(app.git_url)}" target="_blank">${frappe.utils.escape_html(app.git_url)}</a>` : '—';
html += `<tr>
<td><strong>${frappe.utils.escape_html(app.app_name)}</strong></td>
<td><small>${gitUrl}</small></td>
<td>${frappe.utils.escape_html(app.branch || '—')}</td>
<td>
<div class="action-btn-group">
<button class="btn btn-xs btn-primary app-install" data-app="${frappe.utils.escape_html(app.app_name)}">Install</button>
<button class="btn btn-xs btn-default app-uninstall" data-app="${frappe.utils.escape_html(app.app_name)}">Uninstall</button>
</div>
</td>
</tr>`;
});

html += '</tbody></table>';
$wrapper.html(html);
this.bind_app_row_actions(sites);
},
});
}"""

new_render_apps = """render_apps_table(apps) {
const $wrapper = this.$container.find('#apps-table-wrapper');

// Also load sites for the install dropdown
frappe.call({
method: 'bench_manager.api.list_sites',
callback: (r) => {
const sites = (r.message || []).map((s) => s.site_name);
let html = `<table class="bench-table">
<thead><tr>
<th>App Name</th><th>Git URL</th><th>Branch</th><th>Actions</th>
</tr></thead><tbody>`;

apps.forEach((app) => {
// Determine app source category
let source = 'custom';
let sourceBadge = '<span class="badge-source badge-source-custom">Custom</span>';

if (app.git_url) {
if (app.git_url.includes('github.com/frappe/')) {
source = 'frappe_store';
sourceBadge = '<span class="badge-source badge-source-frappe"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2L3 7v11a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-3-5z"/><line x1="3" y1="7" x2="21" y2="7"/><path d="M16 11a4 4 0 0 1-8 0"/></svg> Frappe</span>';
} else {
source = 'git';
sourceBadge = '<span class="badge-source badge-source-git"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg> Git</span>';
}
} else if (app.app_name === 'frappe') {
source = 'frappe_store';
sourceBadge = '<span class="badge-source badge-source-frappe"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2L3 7v11a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-3-5z"/><line x1="3" y1="7" x2="21" y2="7"/><path d="M16 11a4 4 0 0 1-8 0"/></svg> Frappe</span>';
}

const gitUrl = app.git_url ? `<a href="${frappe.utils.escape_html(app.git_url)}" target="_blank">${frappe.utils.escape_html(app.git_url)}</a>` : '—';
html += `<tr data-source="${source}">
<td>
<div style="display:flex; flex-direction:column; gap:4px;">
<strong>${frappe.utils.escape_html(app.app_name)}</strong>
${sourceBadge}
</div>
</td>
<td><small>${gitUrl}</small></td>
<td>${frappe.utils.escape_html(app.branch || '—')}</td>
<td>
<div class="action-btn-group">
<button class="btn btn-xs btn-primary app-install" data-app="${frappe.utils.escape_html(app.app_name)}">Install</button>
<button class="btn btn-xs btn-default app-uninstall" data-app="${frappe.utils.escape_html(app.app_name)}">Uninstall</button>
</div>
</td>
</tr>`;
});

html += '</tbody></table>';
$wrapper.html(html);
this.bind_app_row_actions(sites);

// Re-apply current filter
const activeFilter = this.$container.find('.app-filter-chip.active').data('filter') || 'all';
if (activeFilter !== 'all') {
this.apply_app_filter(activeFilter);
}
},
});
}"""

content = content.replace(old_render_apps, new_render_apps)

with open('/home/praveen/frappe-bench/apps/bench_manager/bench_manager/bench_manager/page/bench_dashboard/bench_dashboard.js', 'w') as f:
    f.write(content)
