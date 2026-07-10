import logging
logger = logging.getLogger(__name__)
import os
import re
import sys
import json
import traceback
from datetime import datetime as _dt

from flask_login import login_required
from flask import abort, Blueprint, current_app, render_template, request, url_for, jsonify
from src.axiom_client import get_devices_by_chipset
from dashboard_common import get_chip_name_for_target, is_axiom_enabled_for_target
from qdt_client import get_rework_info_from_qdt
import device_summary_service as ds_svc

from pathlib import Path

device_summary_api_bp = Blueprint("device_summary_api_bp", __name__)

# ------ pending device rows waiting for Excel unlock ------
_pending_device_rows = {}  # target_name -> list of {row}


def _get_excel_lock_info(path):
    """Return (is_locked, locked_by). Detects ~$filename.xlsx owner file."""
    owner = os.path.join(os.path.dirname(path), '~$' + os.path.basename(path))
    if os.path.exists(owner):
        try:
            with open(owner, 'rb') as f:
                data = f.read()
            raw  = data[8:]
            name = raw.split(b'\x00\x00')[0].decode('utf-16-le', errors='ignore').strip()
            return True, name or 'another user'
        except Exception:
            return True, 'another user'
    return False, None


# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# NEW: Excel-driven Device Summary APIs
# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


@device_summary_api_bp.route('/api/ds/<string:target_name>/upload', methods=['POST'])
@login_required
def api_ds_upload_excel(target_name):
    """Upload a Device Summary Excel - stores managed copy, hides path from UI."""
    try:
        from werkzeug.utils import secure_filename
        upload = request.files.get('file')
        if not upload or not upload.filename:
            return jsonify({'success': False, 'message': 'Please choose an Excel file to upload.'}), 400
        original_name = secure_filename(upload.filename) or 'devices.xlsx'
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ('.xlsx', '.xlsm'):
            return jsonify({'success': False, 'message': 'Please upload an .xlsx or .xlsm file.'}), 400

        dirs = ds_svc.get_managed_target_dirs(target_name)
        upload_dir = dirs.get('devices') or dirs.get('base')
        os.makedirs(upload_dir, exist_ok=True)
        timestamp = _dt.utcnow().strftime('%Y%m%d_%H%M%S')
        stored_name = f'{target_name}_{timestamp}_{original_name}'
        stored_path = os.path.join(upload_dir, stored_name)
        upload.save(stored_path)

        sheet_names = ds_svc.get_sheet_names(stored_path)
        devices_sheet = next((s for s in sheet_names if 'device' in s.lower()), sheet_names[0] if sheet_names else '')

        ds_svc.save_ds_excel_config(
            target_name,
            excel_path=stored_path,
            summary_sheet=devices_sheet,
            devices_sheet=devices_sheet,
            data_mode='excel',
        )
        return jsonify({
            'success': True,
            'message': f'Uploaded {original_name}.',
            'sheet_names': sheet_names,
            'devices_sheet': devices_sheet,
            'original_filename': original_name,
        })
    except Exception as e:
        logger.exception('DS upload failed for %s', target_name)
        return jsonify({'success': False, 'message': str(e)}), 500

@device_summary_api_bp.route('/api/ds/<string:target_name>/sheets', methods=['POST'])
@login_required
def api_ds_get_sheets(target_name):
    """Return sheet names from an Excel file."""
    try:
        payload    = request.get_json(force=True) or {}
        excel_path = payload.get('excel_path', '')
        sheets     = ds_svc.get_sheet_names(excel_path)
        return jsonify({'success': True, 'sheets': sheets})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@device_summary_api_bp.route('/api/ds/<string:target_name>/config/save', methods=['POST'])
@login_required
def api_ds_save_config(target_name):
    """Save Excel config (path + sheets + mode) for a target."""
    try:
        p             = request.get_json(force=True) or {}
        excel_path    = str(p.get('excel_path')    or '').strip()
        summary_sheet = str(p.get('summary_sheet') or '').strip()
        devices_sheet = str(p.get('devices_sheet') or 'Devices').strip()
        data_mode     = str(p.get('data_mode')     or 'excel').strip()
        # Only create a managed workbook when no path is given (Managed Excel button)
        # When a real path is provided, just save the config --- never touch the existing file
        if not excel_path:
            excel_path, summary_sheet, devices_sheet = ds_svc.ensure_device_summary_workbook(
                target_name, '', summary_sheet, devices_sheet
            )
        saved = ds_svc.save_ds_excel_config(
            target_name, excel_path, summary_sheet, devices_sheet, data_mode
        )
        return jsonify({'success': True, 'config': saved})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@device_summary_api_bp.route('/api/ds/<string:target_name>/debug', methods=['GET'])
@login_required
def api_ds_debug(target_name):
    """Dump raw Excel parse info --- devices sheet headers + aggregation result."""
    try:
        import openpyxl
        cfg           = ds_svc.get_ds_excel_config(target_name) or {}
        excel_path    = cfg.get('excel_path', '')
        devices_sheet = cfg.get('devices_sheet', '')
        path          = ds_svc._normalize_path(excel_path)

        if not path or not os.path.exists(path):
            return jsonify({'error': f'Excel not found: {path}', 'cfg': cfg}), 404

        # Read raw headers + first 5 rows from devices sheet
        wb = openpyxl.load_workbook(path, data_only=True)
        dev_ws = wb[devices_sheet] if devices_sheet in wb.sheetnames else None
        raw_headers = []
        sample_rows = []
        if dev_ws:
            mm = ds_svc._build_merge_map(dev_ws)
            raw_headers = [ds_svc._cv(dev_ws, mm, 1, c) for c in range(1, dev_ws.max_column + 1)]
            for rn in range(2, min(dev_ws.max_row + 1, 7)):
                sample_rows.append([ds_svc._cv(dev_ws, mm, rn, c) for c in range(1, dev_ws.max_column + 1)])
        wb.close()

        # Try building the deployment table
        sw_table    = None
        parse_error = None
        try:
            sw_table = ds_svc.build_deployment_table_from_devices(excel_path, devices_sheet)
        except Exception as e:
            parse_error = str(e)

        return jsonify({
            'excel_path':    path,
            'devices_sheet': devices_sheet,
            'config':        cfg,
            'raw_headers':   raw_headers,
            'sample_rows':   sample_rows,
            'parse_error':   parse_error,
            'sw_table_sites':     (sw_table or {}).get('sites'),
            'sw_table_row_count': len((sw_table or {}).get('rows', [])),
            'grand_del':          (sw_table or {}).get('grand_del'),
        })
    except Exception as exc:
        return jsonify({'error': str(exc), 'trace': traceback.format_exc()}), 500


@device_summary_api_bp.route('/api/ds/<string:target_name>/refresh', methods=['POST', 'GET'])
@login_required
def api_ds_refresh_from_excel(target_name):
    """Force a fresh parse/recompile of Device Summary dashboard data from Excel."""
    try:
        ds_svc.get_or_create_device_excel_config(target_name)
        data = ds_svc.load_page_data(target_name)
        sw_table = data.get('sw_table') or {}
        return jsonify({
            'success': True,
            'excel_path': data.get('excel_path', ''),
            'summary_sheet': data.get('summary_sheet', ''),
            'devices_sheet': data.get('devices_sheet', ''),
            'deployment_total': sw_table.get('grand_del', 0),
            'deployment_deployed_total': sw_table.get('grand_dep', 0),
            'devices_total': data.get('devices_total', 0),
            'summary_rows': len(sw_table.get('rows') or []),
            'page_error': data.get('page_error', ''),
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@device_summary_api_bp.route('/api/ds/<string:target_name>/devices/list', methods=['GET'])
@login_required
def api_ds_devices_list(target_name):
    """Return a fresh devices list from the configured/managed Excel sheet."""
    try:
        ds_svc.get_or_create_device_excel_config(target_name)
        data = ds_svc.load_page_data(target_name)
        return jsonify({
            'success':  True,
            'headers':  data['devices_headers'],
            'rows':     data['devices_rows'],
            'total':    data['devices_total'],
            'mode':     data['data_mode'],
            'excel_path': data['excel_path'],
            'devices_sheet': data['devices_sheet'],
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@device_summary_api_bp.route('/api/ds/<string:target_name>/devices/add', methods=['POST'])
@login_required
def api_ds_add_device(target_name):
    """Add a device row and save it to the configured/managed Excel devices sheet."""
    try:
        p   = request.get_json(force=True) or {}
        row = [str(v) for v in (p.get('row') or [])]
        cfg = ds_svc.get_or_create_device_excel_config(target_name)
        path = ds_svc._normalize_path(cfg.get('excel_path', ''))
        locked, locked_by = _get_excel_lock_info(path)
        if locked:
            _pending_device_rows.setdefault(target_name, []).append({'op': 'add', 'row': row})
            return jsonify({'success': True, 'excel': 'locked', 'locked': True, 'locked_by': locked_by})

        saved = ds_svc.append_device_row(target_name, row)
        data = ds_svc.load_page_data(target_name)
        return jsonify({
            'success': True,
            'total': data['devices_total'],
            'row': row,
            'excel': 'saved',
            'locked': False,
            'locked_by': None,
            'headers': data['devices_headers'],
            'rows': data['devices_rows'],
            'excel_path': saved['excel_path'],
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@device_summary_api_bp.route('/api/ds/<string:target_name>/devices/retry_sync', methods=['POST'])
@login_required
def api_ds_retry_sync(target_name):
    """Retry writing pending device rows to Excel after lock is released."""
    try:
        pending = _pending_device_rows.get(target_name, [])
        if not pending:
            return jsonify({'success': True, 'message': 'Nothing to sync.'})
        cfg = ds_svc.get_or_create_device_excel_config(target_name)
        path = ds_svc._normalize_path(cfg.get('excel_path', ''))
        locked, locked_by = _get_excel_lock_info(path)
        if locked:
            return jsonify({'success': False, 'locked': True, 'locked_by': locked_by,
                            'message': f'Still locked by {locked_by}.'})
        synced = 0
        for item in pending:
            if item.get('op', 'add') == 'add':
                ds_svc.append_device_row(target_name, item['row'])
                synced += 1
        _pending_device_rows[target_name] = []
        return jsonify({'success': True, 'message': f'Synced {synced} device(s) to Excel.'})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@device_summary_api_bp.route('/api/ds/<string:target_name>/devices/delete', methods=['POST'])
@login_required
def api_ds_delete_device(target_name):
    """Delete a device row by 0-based index from the Excel devices sheet."""
    try:
        p = request.get_json(force=True) or {}
        idx = int(p.get('index', -1))
        cfg = ds_svc.get_or_create_device_excel_config(target_name)
        path = ds_svc._normalize_path(cfg.get('excel_path', ''))
        locked, locked_by = _get_excel_lock_info(path)
        if locked:
            return jsonify({'success': False, 'locked': True, 'locked_by': locked_by,
                            'message': f'Excel is locked by {locked_by}.'}), 423
        deleted = ds_svc.delete_device_row(target_name, idx)
        data = ds_svc.load_page_data(target_name)
        return jsonify({'success': True, 'total': data['devices_total'], 'removed': deleted['removed'],
                        'headers': data['devices_headers'], 'rows': data['devices_rows']})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


@device_summary_api_bp.route('/api/ds/<string:target_name>/devices/edit', methods=['POST'])
@login_required
def api_ds_edit_device(target_name):
    """Edit a device row by 0-based index in the Excel devices sheet."""
    try:
        p = request.get_json(force=True) or {}
        idx = int(p.get('index', -1))
        row = [str(v) for v in (p.get('row') or [])]
        cfg = ds_svc.get_or_create_device_excel_config(target_name)
        path = ds_svc._normalize_path(cfg.get('excel_path', ''))
        locked, locked_by = _get_excel_lock_info(path)
        if locked:
            return jsonify({'success': False, 'locked': True, 'locked_by': locked_by,
                            'message': f'Excel is locked by {locked_by}.'}), 423
        ds_svc.edit_device_row(target_name, idx, row)
        data = ds_svc.load_page_data(target_name)
        return jsonify({'success': True, 'total': data['devices_total'], 'row': row,
                        'headers': data['devices_headers'], 'rows': data['devices_rows']})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400


# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# EXISTING Axiom-based APIs (unchanged below)
# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


def _app_root_dir():
    """Return the directory next to the exe (frozen) or project root (dev)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _axiom_cache_dir():
    root = os.path.join(_app_root_dir(), 'static', 'axiom_cache')
    os.makedirs(root, exist_ok=True)
    return root


def _cache_path_for(chipset, pdt_type):
    chip = (chipset or '').strip().upper() or 'UNKNOWN'
    pdt = (pdt_type or 'SWPDT').strip().upper()
    return os.path.join(_axiom_cache_dir(), "{}_{}.json".format(chip, pdt))


def _unified_cache_path(chipset, pdt_type):
    chip = (chipset or '').strip().upper() or 'UNKNOWN'
    pdt = (pdt_type or 'SWPDT').strip().upper()
    return os.path.join(_axiom_cache_dir(), "{}_{}_unified.json".format(chip, pdt))


def _overrides_dir():
    root = os.path.join(_axiom_cache_dir(), 'overrides')
    os.makedirs(root, exist_ok=True)
    return root


def _sw_del_override_path(chipset):
    return os.path.join(_overrides_dir(), "{}_SWPDT_DEL.json".format((chipset or '').strip().upper()))


def _hw_metrics_override_path(chipset):
    return os.path.join(_overrides_dir(), "{}_HWPDT_metrics.json".format((chipset or '').strip().upper()))


def _device_pool_override_path(chipset, pdt_type):
    chip = (chipset or '').strip().upper()
    pdt  = (pdt_type or 'SWPDT').strip().upper()
    return os.path.join(_overrides_dir(), "{}_{}_pool.json".format(chip, pdt))


def _load_device_pool_overrides(chipset, pdt_type):
    data = _load_json(_device_pool_override_path(chipset, pdt_type))
    if not data:
        return {'removed': [], 'edits': {}}
    return data


def _save_device_pool_overrides(chipset, pdt_type, payload):
    payload['chipset']  = (chipset or '').strip().upper()
    payload['pdt_type'] = (pdt_type or 'SWPDT').strip().upper()
    payload['saved_at'] = _dt.utcnow().isoformat() + 'Z'
    _save_json(_device_pool_override_path(chipset, pdt_type), payload)


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def _save_json(path, payload):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def _merge_axiom_qdt_devices(chip_name, pdt_type, devices):
    qdt_rows = []
    qdt_ok = True
    try:
        qdt_rows = get_rework_info_from_qdt(chip_name) or []
    except Exception as _qdt_err:
        qdt_ok = False
        logger.info(f'[DEVICE SUMMARY] QDT fetch failed (non-fatal): {_qdt_err}')
        qdt_rows = []

    qdt_map = {}
    for item in qdt_rows:
        # Match by serial_number OR u_serial_number_qc_mfg
        for key in ('SERIAL_NO', 'SERIAL_NO_QC'):
            serial = str(item.get(key) or '').strip().upper()
            if serial:
                qdt_map[serial] = item
                break

    merged_devices = []
    for dev in devices or []:
        merged = dict(dev)
        serial = str(
            dev.get('serialNumber')
            or dev.get('serial_number')
            or dev.get('serial_no')
            or dev.get('serial')
            or dev.get('SERIAL_NO')
            or ''
        ).strip().upper()
        q_item = qdt_map.get(serial)
        if q_item:
            merged['qdt_rework_info'] = str(q_item.get('REWORK_INFO') or '').strip()
            merged['qdt_location']    = str(q_item.get('LOCATION') or '').strip()
            merged['qdt_asset_tag']   = str(q_item.get('ASSET_TAG') or '').strip()
            merged['qdt_assigned_to'] = str(q_item.get('ASSIGNED_TO') or '').strip()
            merged['qdt_condition']   = str(q_item.get('DEVICE_CONDITION') or '').strip()
            merged['qdt_mes_build']   = str(q_item.get('MES_BUILD') or '').strip()
            merged['qdt_model_desc']  = str(q_item.get('MODEL_DESC') or '').strip()
            # Parse MCN and storage from dv_model string e.g.
            # "DTP, MTP, INT-SECURE SM4850, 6GB LP4X + 128GB UFS2.2, ..."
            model_desc = merged['qdt_model_desc']
            import re as _re
            mcn_m = _re.search(r'(\d{2}-\d{5}-\d{3,4})', model_desc)
            merged['qdt_mcn'] = mcn_m.group(1) if mcn_m else ''
            ufs_m = _re.search(r'(\d+GB\s+UFS[\d\.]*)', model_desc, _re.I)
            lp_m  = _re.search(r'(\d+GB\s+LP[\dA-Z]+)', model_desc, _re.I)
            merged['qdt_storage'] = (ufs_m or lp_m).group(1).strip() if (ufs_m or lp_m) else ''
        merged_devices.append(merged)

    return {
        'chipset': chip_name.upper(),
        'pdt_type': (pdt_type or 'SWPDT').strip().upper(),
        'saved_at': _dt.utcnow().isoformat() + 'Z',
        'count': len(merged_devices),
        'devices': merged_devices,
        'qdt_ok': qdt_ok,
    }


@device_summary_api_bp.route('/api/device_summary_data/<string:target_name>')
@login_required
def api_device_summary_data(target_name):
    pdt_type = (request.args.get('pdt') or 'SWPDT').strip().upper()
    refresh = (request.args.get('refresh') or '0').strip() in ('1', 'true', 'True')

    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        return jsonify({'success': False, 'message': "No chip name configured for {}".format(target_name)}), 400

    unified_path = _unified_cache_path(chip_name, pdt_type)
    legacy_path = _cache_path_for(chip_name, pdt_type)
    data = None if refresh else _load_json(unified_path) or _load_json(legacy_path)
    source = 'cache'

    if refresh:
        data = None

    if data is None:
        if not is_axiom_enabled_for_target(target_name):
            return jsonify({'success': False, 'message': "Axiom not enabled for {}".format(chip_name)}), 400
        try:
            devices = get_devices_by_chipset(chip_name, pdt_type=pdt_type, include_site_details=True) or []
            if pdt_type == 'SWPDT':
                devices = [
                    d for d in devices
                    if not str(
                        (d.get('_raw') or {}).get('taxonomyPath')
                        or d.get('taxonomy_path')
                        or ''
                    ).strip().upper().startswith('/PDT/QIPL/HW')
                ]
            data = _merge_axiom_qdt_devices(chip_name, pdt_type, devices)
            _save_json(unified_path, data)
            _save_json(legacy_path, data)
            source = 'axiom' if not data.get('qdt_ok', True) else 'axiom+qdt'
        except OSError as e:
            # Missing credentials --- tell the UI clearly instead of 500
            return jsonify({
                'success': False,
                'message': str(e),
                'hint': 'Add AXIOM_CLIENT_ID and AXIOM_CLIENT_SECRET to the .env file next to BuddyApp.exe and restart.'
            }), 503
        except Exception as e:
            logger.debug(traceback.format_exc())
            return jsonify({'success': False, 'message': str(e)}), 500

    payload = {'success': True, 'source': source, 'cache_file': unified_path, 'legacy_cache_file': legacy_path}
    payload.update(data)
    return jsonify(payload)


@device_summary_api_bp.route('/api/device_summary_data/<string:target_name>/sync_status')
@login_required
def api_sync_status(target_name):
    """Return current cache status for both PDT types --- used by the sync banner to show progress."""
    chip_name = get_chip_name_for_target(target_name) or ''
    result = {}
    for pdt in ('SWPDT', 'HWPDT'):
        path = _unified_cache_path(chip_name, pdt)
        legacy = _cache_path_for(chip_name, pdt)
        data = _load_json(path) or _load_json(legacy)
        if data:
            result[pdt] = {
                'ready': True,
                'count': data.get('count', len(data.get('devices', []))),
                'saved_at': data.get('saved_at', ''),
                'qdt_ok': data.get('qdt_ok', True),
                'source': data.get('source', 'cache'),
            }
        else:
            result[pdt] = {'ready': False, 'count': 0}
    return jsonify({'success': True, 'chip_name': chip_name, 'status': result})



@login_required
def api_device_taxonomy_data(target_name):
    path = request.args.get('path', '').strip()
    if not path:
        return jsonify({'success': False, 'message': "Taxonomy path is required"}), 400
        
    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        return jsonify({'success': False, 'message': "No chip name configured for {}".format(target_name)}), 400

    hw_data = _load_json(_unified_cache_path(chip_name, 'HWPDT')) or _load_json(_cache_path_for(chip_name, 'HWPDT')) or {'devices': []}
    sw_data = _load_json(_unified_cache_path(chip_name, 'SWPDT')) or _load_json(_cache_path_for(chip_name, 'SWPDT')) or {'devices': []}

    filtered_devices = []
    path_upper = path.strip().upper()
    for device in hw_data.get('devices', []) + sw_data.get('devices', []):
        if str(device.get('taxonomy_path') or '').strip().upper() == path_upper:
            filtered_devices.append(device)
    
    return jsonify({
        'success': True,
        'path': path,
        'chipset': chip_name,
        'count': len(filtered_devices),
        'devices': filtered_devices,
    })


@device_summary_api_bp.route('/api/device_summary_data/save_sw_del/<string:target_name>', methods=['POST'])
@login_required
def api_save_sw_del_data(target_name):
    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        return jsonify({'success': False, 'message': "No chip name configured for {}".format(target_name)}), 400
    payload = request.get_json(force=True) or {}
    data = {
        'chipset': chip_name.upper(),
        'saved_at': _dt.utcnow().isoformat() + 'Z',
        'rows': payload.get('rows') or [],
    }
    _save_json(_sw_del_override_path(chip_name), data)
    return jsonify({'success': True, 'saved_at': data['saved_at']})


@device_summary_api_bp.route('/api/device_summary_data/save_hw_metrics/<string:target_name>', methods=['POST'])
@login_required
def api_save_hw_metrics_data(target_name):
    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        return jsonify({'success': False, 'message': "No chip name configured for {}".format(target_name)}), 400
    payload = request.get_json(force=True) or {}
    data = {
        'chipset': chip_name.upper(),
        'saved_at': _dt.utcnow().isoformat() + 'Z',
        'columns': payload.get('columns') or ['REV0', 'REV1', 'Part Type', 'Total'],
        'rows': payload.get('rows') or [],
    }
    _save_json(_hw_metrics_override_path(chip_name), data)
    return jsonify({'success': True, 'saved_at': data['saved_at']})


# ---------------------------------------------------------------------------
# Device Pool Override APIs  (remove / edit / restore per device)
# ---------------------------------------------------------------------------

@device_summary_api_bp.route('/api/device_pool/<string:target_name>/remove', methods=['POST'])
@login_required
def api_device_pool_remove(target_name):
    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        return jsonify({'success': False, 'message': 'No chip name for target'}), 400
    body      = request.get_json(force=True) or {}
    device_id = str(body.get('device_id') or '').strip()
    pdt_type  = str(body.get('pdt_type') or 'SWPDT').strip().upper()
    if not device_id:
        return jsonify({'success': False, 'message': 'device_id required'}), 400
    ov = _load_device_pool_overrides(chip_name, pdt_type)
    removed = list(ov.get('removed') or [])
    if device_id not in removed:
        removed.append(device_id)
    ov['removed'] = removed
    _save_device_pool_overrides(chip_name, pdt_type, ov)
    return jsonify({'success': True, 'removed_count': len(removed)})


@device_summary_api_bp.route('/api/device_pool/<string:target_name>/restore', methods=['POST'])
@login_required
def api_device_pool_restore(target_name):
    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        return jsonify({'success': False, 'message': 'No chip name for target'}), 400
    body      = request.get_json(force=True) or {}
    device_id = str(body.get('device_id') or '').strip()
    pdt_type  = str(body.get('pdt_type') or 'SWPDT').strip().upper()
    ov = _load_device_pool_overrides(chip_name, pdt_type)
    ov['removed'] = [d for d in (ov.get('removed') or []) if d != device_id]
    _save_device_pool_overrides(chip_name, pdt_type, ov)
    return jsonify({'success': True, 'removed_count': len(ov['removed'])})


@device_summary_api_bp.route('/api/device_pool/<string:target_name>/edit', methods=['POST'])
@login_required
def api_device_pool_edit(target_name):
    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        return jsonify({'success': False, 'message': 'No chip name for target'}), 400
    body     = request.get_json(force=True) or {}
    pdt_type = str(body.get('pdt_type') or 'SWPDT').strip().upper()
    edits    = body.get('edits') or {}
    ov = _load_device_pool_overrides(chip_name, pdt_type)
    existing = dict(ov.get('edits') or {})
    for dev_id, fields in edits.items():
        if not dev_id:
            continue
        existing[dev_id] = {
            'mcn_display':         str(fields.get('mcn_display') or '').strip(),
            'storage_display':     str(fields.get('storage_display') or '').strip(),
            'location_display':    str(fields.get('location_display') or '').strip(),
            'rework_info_display': str(fields.get('rework_info_display') or '').strip(),
        }
    ov['edits'] = existing
    _save_device_pool_overrides(chip_name, pdt_type, ov)
    return jsonify({'success': True, 'edited_count': len(existing)})


@device_summary_api_bp.route('/api/device_pool/<string:target_name>/overrides')
@login_required
def api_device_pool_overrides(target_name):
    chip_name = get_chip_name_for_target(target_name) or ''
    pdt_type  = (request.args.get('pdt') or 'SWPDT').strip().upper()
    ov = _load_device_pool_overrides(chip_name, pdt_type)
    return jsonify({'success': True, 'chip_name': chip_name, 'pdt_type': pdt_type,
                    'removed': ov.get('removed', []), 'edits': ov.get('edits', {})})




@device_summary_api_bp.route('/device-summary-devices/<string:target_name>')
@login_required


def device_summary_devices_page(target_name):
    pdt_type = (request.args.get("pdt") or "SWPDT").strip().upper()

    if not target_name:
        abort(400, description="Invalid target")

    # Resolve chip_name from target config (e.g. ALDABRA -> SM4850)
    chip_name = get_chip_name_for_target(target_name) or ''
    if not chip_name:
        # fallback: use target_name itself as chip_name
        chip_name = str(target_name).strip().upper()
    chip_name = chip_name.strip().upper()

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def _first_non_empty(*values):
        for v in values:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return ""

    def _normalize_storage(value):
        s = str(value or "").strip()
        if not s:
            return ""
        s = re.sub(r"\s+", " ", s)
        s = s.replace("UFS ", "UFS")
        s = s.replace("LPDDR ", "LPDDR")
        return s.strip()

    def _parse_from_description(desc):
        text = str(desc or "").strip()
        if not text:
            return {"mcn": "", "ddr": "", "storage": ""}

        t = text.upper()
        t = t.replace("+", " + ")
        t = t.replace("/", " / ")
        t = t.replace(",", " , ")
        t = re.sub(r"[\(\)\[\]\|]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()

        mcn = ""
        ddr = ""
        storage = ""

        # MCN
        m = re.search(r"\bMCN[-_\s:]*([A-Z0-9\-]+)\b", t)
        if m:
            val = m.group(1).strip()
            mcn = val if val.startswith("MCN") else f"MCN{val}"

        # DDR
        ddr_patterns = [
            r"\b\d+\s*GB\s*(?:LPDDR[0-9A-Z\.]+|DDR[0-9A-Z\.]+)\b",
            r"\b(?:LPDDR[0-9A-Z\.]+|DDR[0-9A-Z\.]+)\s*\d+\s*GB\b",
            r"\b(?:LPDDR[0-9A-Z\.]+|DDR[0-9A-Z\.]+)\b",
        ]
        for pat in ddr_patterns:
            m = re.search(pat, t)
            if m:
                ddr = re.sub(r"\s+", " ", m.group(0)).strip()
                break

        # Storage
        storage_patterns = [
            r"\b\d+\s*GB\s*UFS\s*[0-9\.]*\b",
            r"\bUFS\s*[0-9\.]+\b",
            r"\bUFS\b",
            r"\b\d+\s*GB\s*EMMC\s*[0-9\.]*\b",
            r"\bEMMC\s*[0-9\.]+\b",
            r"\bEMMC\b",
        ]
        for pat in storage_patterns:
            m = re.search(pat, t)
            if m:
                storage = re.sub(r"\s+", " ", m.group(0)).strip()
                break

        return {
            "mcn": mcn,
            "ddr": ddr,
            "storage": _normalize_storage(storage),
        }

    def _normalize_taxonomy(taxonomy_path=""):
        return str(taxonomy_path or "").strip()

    def _include_for_pdt(pdt_type_value, taxonomy_path):
        tx = _normalize_taxonomy(taxonomy_path).upper()
        pdt = str(pdt_type_value or "").strip().upper()

        # HWPDT => only /PDT/QIPL/HW
        if pdt == "HWPDT":
            return tx.startswith("/PDT/QIPL/HW")

        # SWPDT => exclude /PDT/QIPL/HW
        return not tx.startswith("/PDT/QIPL/HW")

    def _location_from_values(taxonomy_path="", raw_location=""):
        tx = str(taxonomy_path or "").strip().upper()
        loc = str(raw_location or "").strip().upper()

        # taxonomy mapping --- check most specific first
        # /PDT/QIPL/HW is excluded for SWPDT, but map it to QIPL if it ever appears
        if "/PDT/QIPL" in tx:
            return "QIPL"
        if "/PDT/CHINA" in tx:
            return "CH"
        if "/PDT/SD" in tx or "/PDT/SAN DIEGO" in tx or "/PDT/SANDIEGO" in tx:
            return "SD"

        # fallback: derive from physical location path
        if "/AP/SHANGHAI" in loc or "/AP/CHINA" in loc:
            return "CH"
        if "/AP/SAN DIEGO" in loc or "/AP/SANDIEGO" in loc or "/AP/SD" in loc:
            return "SD"
        if "/AP/HYDERABAD" in loc or "QIPL" in loc:
            return "QIPL"

        return ""

    # MCN pattern: 10-XXXXX-XXX or 10-XXXXX-XXXX (followed by non-digit or end)
    _MCN_RE = re.compile(r'\b(\d{2}-\d{5}-\d{3,4})(?!\d)')

    def _extract_mcn(device_obj):
        """Extract MCN (10-XXXXX-XXXX) from _raw.description or qdt_model_desc.
        Never fall back to chipsetRev (V1.0) --- that is a hardware revision, not MCN."""
        raw  = device_obj.get("_raw") or {}
        desc = str(raw.get("description") or device_obj.get("description") or "")
        m = _MCN_RE.search(desc)
        if m:
            return m.group(1)
        # QDT enrichment: parse from dv_model string
        qdt_desc = str(device_obj.get("qdt_model_desc") or "")
        m2 = _MCN_RE.search(qdt_desc)
        if m2:
            return m2.group(1)
        return ""  # blank is better than showing V1.0

    def _extract_storage(device_obj):
        raw = device_obj.get("_raw") or {}
        deps = raw.get("dependencies") or {}

        storage = _first_non_empty(
            device_obj.get("storage_display"),
            device_obj.get("storage"),
            device_obj.get("Storage"),
            device_obj.get("qdt_storage"),
            deps.get("storage Type"),
            deps.get("storage"),
            raw.get("storage"),
        )

        if storage:
            return _normalize_storage(storage)

        desc = _first_non_empty(
            device_obj.get("description"),
            device_obj.get("Description"),
            raw.get("description"),
        )
        parsed = _parse_from_description(desc)
        return _normalize_storage(parsed.get("storage"))

    def _extract_rework_info(device_obj):
        raw = device_obj.get("_raw") or {}
        return _first_non_empty(
            device_obj.get("rework_info_display"),
            device_obj.get("rework_info"),
            device_obj.get("Rework Info"),
            device_obj.get("rework"),
            raw.get("rework_info"),
            raw.get("rework"),
        )

    def _extract_host_pc(device_obj):
        raw = device_obj.get("_raw") or {}
        return _first_non_empty(
            device_obj.get("host_pc"),
            device_obj.get("hostname"),
            device_obj.get("hostName"),
            raw.get("hostname"),
            raw.get("hostName"),
        )

    def _extract_device_id(device_obj):
        raw = device_obj.get("_raw") or {}
        return _first_non_empty(
            device_obj.get("device_id"),
            device_obj.get("device_key"),
            device_obj.get("serialNumber"),
            device_obj.get("serial_number"),
            device_obj.get("serial_no"),
            device_obj.get("serial"),
            raw.get("serialNumber"),
            raw.get("serial_number"),
            raw.get("serial_no"),
            raw.get("serial"),
            device_obj.get("hostname"),
            raw.get("hostname"),
        )

    # ------------------------------------------------------------
    # Load JSON
    # ------------------------------------------------------------
    root = Path(current_app.root_path)
    axiom_cache_dir = root / "static" / "axiom_cache"

    candidate_files = [
        axiom_cache_dir / f"{chip_name}_{pdt_type}_unified.json",
        axiom_cache_dir / f"{chip_name}_{pdt_type}.json",
        root / "cache" / f"{chip_name}_{pdt_type.lower()}_devices.json",
        root / "cache" / f"{chip_name}_{pdt_type}_devices.json",
        root / "cache" / f"{chip_name}_devices.json",
        root / "cache" / f"{chip_name}.json",
        root / "unified_cache" / f"{chip_name}_{pdt_type.lower()}_devices.json",
        root / "unified_cache" / f"{chip_name}.json",
    ]

    json_path = None
    for fp in candidate_files:
        if fp.exists():
            json_path = fp
            break

    if not json_path:
        current_app.logger.warning(
            "No device JSON found for target=%s pdt=%s. Checked: %s",
            chip_name, pdt_type, [str(x) for x in candidate_files]
        )
        # ------ No cache at all: render the "syncing" waiting page ------
        return render_template(
            "device_summary_devices.html",
            target_name=target_name,
            chip_name=chip_name,
            pdt_type=pdt_type,
            devices=[],
            total_devices=0,
            refresh_qdt_url=url_for("dashboard_bp.device_summary_page", target_name=target_name, pdt=pdt_type),
            cache_missing=True,
            sync_api_url=url_for("device_summary_api_bp.api_device_summary_data", target_name=target_name, pdt=pdt_type, refresh=1),
        )
    else:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    raw_devices = data.get("devices") or []

    # ------------------------------------------------------------
    # Build output rows
    # ------------------------------------------------------------
    devices_out = []

    for d in raw_devices:
        raw = d.get("_raw") or {}

        # Always prefer _raw.taxonomyPath --- the top-level taxonomy_path is often just "/PDT"
        taxonomy_path = _first_non_empty(
            raw.get("taxonomyPath"),
            d.get("taxonomy_path"),
            d.get("taxonomy"),
            d.get("taxonomy_display"),
            raw.get("taxonomy_path"),
            raw.get("taxonomy"),
        )

        # HWPDT / SWPDT split --- exclude /PDT/QIPL/HW from SWPDT
        if not _include_for_pdt(pdt_type, taxonomy_path):
            continue

        raw_location = _first_non_empty(
            d.get("location"),
            d.get("site"),
            d.get("lab"),
            raw.get("location"),
            raw.get("site"),
            raw.get("lab"),
        )

        location_display = _location_from_values(taxonomy_path, raw_location)

        device_row = {
            "device_id": _extract_device_id(d),
            "host_pc": _extract_host_pc(d),
            "mcn_display": _extract_mcn(d),
            "storage_display": _extract_storage(d),
            "location_display": location_display,
            "rework_info_display": _extract_rework_info(d),
            "taxonomy_display": taxonomy_path,
        }

        devices_out.append(device_row)

    # sort rows
    devices_out.sort(
        key=lambda x: (
            str(x.get("location_display") or ""),
            str(x.get("host_pc") or ""),
            str(x.get("device_id") or "")
        )
    )

    refresh_url = url_for("dashboard_bp.device_summary_page", target_name=target_name, pdt=pdt_type)

    return render_template(
        "device_summary_devices.html",
        target_name=target_name,
        chip_name=chip_name,
        pdt_type=pdt_type,
        devices=devices_out,
        total_devices=len(devices_out),
        refresh_qdt_url=refresh_url,
    )
