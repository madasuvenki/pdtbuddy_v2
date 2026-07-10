
import logging
logger = logging.getLogger(__name__)
import mysql.connector
from config import QDT_CONFIG

_QDT_PERMISSION_ERROR_CODES = {1142, 1143, 1044}


def get_qdt_connection():
    try:
        conn = mysql.connector.connect(**QDT_CONFIG)
        return conn
    except mysql.connector.Error as e:
        logger.info(f"[QDT] Connection failed (non-fatal): {e}")
        return None


def get_rework_info_from_qdt(chipset):
    """
    Fetch device enrichment info from QDT (EAM) for all devices matching a chipset.
    Queries x_qui_engineering_eam_hardware_asset by dv_model containing the chipset name.
    Returns list of dicts with SERIAL_NO, REWORK_INFO, MCN, STORAGE, LOCATION, ASSET_TAG.
    QDT enrichment is non-critical --- device sync works without it.
    """
    conn = get_qdt_connection()
    if not conn:
        return []

    cursor = conn.cursor(dictionary=True)
    try:
        query = """
            SELECT
                serial_number          AS SERIAL_NO,
                u_serial_number_qc_mfg AS SERIAL_NO_QC,
                asset_tag              AS ASSET_TAG,
                dv_model               AS MODEL_DESC,
                msm_upgrade            AS REWORK_INFO,
                dv_location            AS LOCATION,
                dv_assigned_to         AS ASSIGNED_TO,
                dv_condition           AS DEVICE_CONDITION,
                u_mes_build            AS MES_BUILD,
                u_built_by             AS BUILT_BY,
                dv_scanned_location    AS SCANNED_LOCATION,
                sys_updated_on         AS LAST_UPDATED
            FROM x_qui_engineering_eam_hardware_asset
            WHERE dv_model LIKE %s
              AND serial_number IS NOT NULL
              AND serial_number != ''
        """
        cursor.execute(query, (f'%{chipset}%',))
        rows = cursor.fetchall() or []
        logger.info(f"[QDT] Found {len(rows)} device(s) for chipset '{chipset}'")
        return rows

    except mysql.connector.Error as e:
        if e.errno in _QDT_PERMISSION_ERROR_CODES:
            logger.info(f"[QDT] Permission denied for chipset '{chipset}' (non-fatal, skipping QDT enrichment).")
        else:
            logger.info(f"[QDT] Query failed for chipset '{chipset}' (non-fatal): {e}")
        return []
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


def get_device_info_by_serial(serial_number):
    """
    Fetch full device info from QDT for a single serial number.
    Used for on-demand lookup of a specific device.
    """
    conn = get_qdt_connection()
    if not conn:
        return None

    cursor = conn.cursor(dictionary=True)
    try:
        query = """
            SELECT
                serial_number          AS SERIAL_NO,
                u_serial_number_qc_mfg AS SERIAL_NO_QC,
                asset_tag              AS ASSET_TAG,
                dv_model               AS MODEL_DESC,
                msm_upgrade            AS REWORK_INFO,
                dv_location            AS LOCATION,
                dv_assigned_to         AS ASSIGNED_TO,
                dv_condition           AS DEVICE_CONDITION,
                u_mes_build            AS MES_BUILD,
                u_built_by             AS BUILT_BY,
                dv_scanned_location    AS SCANNED_LOCATION,
                sys_updated_on         AS LAST_UPDATED
            FROM x_qui_engineering_eam_hardware_asset
            WHERE serial_number = %s
               OR u_serial_number_qc_mfg = %s
            LIMIT 1
        """
        cursor.execute(query, (serial_number, serial_number))
        return cursor.fetchone()

    except mysql.connector.Error as e:
        if e.errno in _QDT_PERMISSION_ERROR_CODES:
            logger.info(f"[QDT] Permission denied for serial '{serial_number}' (non-fatal).")
        else:
            logger.info(f"[QDT] Query failed for serial '{serial_number}' (non-fatal): {e}")
        return None
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass

