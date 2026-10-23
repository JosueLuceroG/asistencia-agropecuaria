import urllib.request
import urllib.error
import json
import sys
import os

IP = os.environ.get("HIKVISION_IP", "")
PORT = int(os.environ.get("HIKVISION_PORT", "80"))
USER = os.environ.get("HIKVISION_USER", "admin")
PASSWORD = os.environ.get("HIKVISION_PASSWORD", "")

BASE = f"http://{IP}:{PORT}"


def verificar_configuracion():
    faltantes = []
    if not IP:
        faltantes.append("HIKVISION_IP")
    if not PASSWORD:
        faltantes.append("HIKVISION_PASSWORD")
    if faltantes:
        raise RuntimeError(
            "Define las variables de entorno requeridas: " + ", ".join(faltantes)
        )

def test_conexion():
    print("=" * 60)
    print("PRUEBA 1: Device Info")
    print("=" * 60)
    try:
        verificar_configuracion()
        passman = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        passman.add_password(None, BASE, USER, PASSWORD)
        opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(passman),
            urllib.request.HTTPBasicAuthHandler(passman)
        )
        req = urllib.request.Request(f"{BASE}/ISAPI/System/deviceInfo?format=json")
        resp = opener.open(req, timeout=8)
        raw = resp.read().decode("utf-8", errors="replace")
        print(f"Status: {resp.status}")
        print(f"Response ({len(raw)} chars):")
        print(raw[:1000])
        print()
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def test_eventos():
    print("=" * 60)
    print("PRUEBA 2: Buscar eventos ACS (JSON)")
    print("=" * 60)
    try:
        verificar_configuracion()
        passman = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        passman.add_password(None, BASE, USER, PASSWORD)
        opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(passman),
            urllib.request.HTTPBasicAuthHandler(passman)
        )
        # Hikvision solo acepta XML en el POST, NO JSON
        xml_payload = f'''<?xml version="1.0" encoding="UTF-8"?>
<AcsEventCond version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
<searchID>test123</searchID>
<searchResultPosition>0</searchResultPosition>
<maxResults>10</maxResults>
<major>5</major>
<minor>0</minor>
<startTime>2026-05-28T00:00:00</startTime>
<endTime>2026-05-30T23:59:59</endTime>
</AcsEventCond>'''
        data = xml_payload.encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}/ISAPI/AccessControl/AcsEvent?format=json",
            data=data,
            headers={"Content-Type": "application/xml", "Accept": "application/xml, */*"},
            method="POST"
        )
        resp = opener.open(req, timeout=18)
        raw = resp.read().decode("utf-8", errors="replace")
        print(f"Status: {resp.status}")
        print(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
        print(f"Response ({len(raw)} chars):")
        print(raw[:2000])
        print()

        # Try to parse as JSON
        try:
            jdata = json.loads(raw)
            print(">>> ES JSON valido!")
            if "AcsEvent" in jdata:
                print(f">>> Tiene 'AcsEvent': {json.dumps(jdata['AcsEvent'], indent=2)[:500]}")
            if "InfoList" in str(jdata):
                print(">>> Tiene InfoList en alguna parte")
        except:
            print(">>> NO es JSON - es XML u otro formato")
            if "<" in raw[:100]:
                print(">>> PARECE XML")
                # Extract InfoList items count
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(raw)
                    # Count InfoList items
                    for elem in root.iter():
                        if 'InfoList' in elem.tag or 'infoList' in elem.tag:
                            children = list(elem)
                            print(f">>> InfoList tiene {len(children)} eventos")
                            if children:
                                first = children[0]
                                print(f">>> Primer evento:")
                                for field in first:
                                    tag = field.tag.split('}')[1] if '}' in field.tag else field.tag
                                    print(f"    {tag} = {field.text}")
                            break
                except Exception as e:
                    print(f">>> Error parseando XML: {e}")

        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        print(f"HTTP ERROR {e.code}: {body}")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    try:
        verificar_configuracion()
    except RuntimeError as exc:
        print(f"CONFIGURACION REQUERIDA: {exc}")
        sys.exit(2)
    ok = test_conexion()
    if ok:
        test_eventos()
    else:
        print("\nNo se pudo conectar al Hikvision. Verifica:")
        print("  - Que HIKVISION_IP sea correcta")
        print("  - Que HIKVISION_USER y HIKVISION_PASSWORD sean correctos")
        print("  - Que el dispositivo esté en la red")
