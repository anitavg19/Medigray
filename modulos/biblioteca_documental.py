"""Bloque 4: índice documental y verificación no destructiva (no modifica archivos)."""
from pathlib import Path
import hashlib
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse


def register(m):
    app = m.app

    @app.get('/biblioteca-documental', response_class=HTMLResponse)
    def biblioteca(req: Request, company: int = 0, q: str = '', country: int = 0):
        u = m.require(req)
        if company:
            m.company_access(u, company)
            m.module_access(u, company, 'adjuntos')
        with m.conn() as c:
            companies = c.execute('''SELECT co.* FROM companies co JOIN user_companies uc
                ON uc.company_id=co.id WHERE uc.user_id=? ORDER BY co.name''', (u['id'],)).fetchall()
            countries = c.execute('SELECT id,name FROM countries ORDER BY name').fetchall()
            records = c.execute('''SELECT v.id version_id, v.revision, v.filename pdf_original,
                a.id art_id, a.company_id, a.sap_code, a.master_code, a.product,
                co.name company_name,
                (SELECT COUNT(*) FROM mg2_version_documents d WHERE d.version_id=v.id) document_count,
                (SELECT group_concat(DISTINCT p.name) FROM country_versions cv
                 JOIN countries p ON p.id=cv.country_id WHERE cv.version_id=v.id) country_names,
                (SELECT group_concat(DISTINCT cv.status) FROM country_versions cv
                 WHERE cv.version_id=v.id) statuses
                FROM versions v JOIN arts a ON a.id=v.art_id
                JOIN companies co ON co.id=a.company_id
                WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                  AND (?=0 OR a.company_id=?)
                  AND (a.sap_code LIKE ? OR a.master_code LIKE ? OR a.product LIKE ?)
                  AND (?=0 OR EXISTS (SELECT 1 FROM country_versions cv
                       WHERE cv.version_id=v.id AND cv.country_id=?))
                ORDER BY a.sap_code, v.id DESC LIMIT 300''',
                (u['id'], company, company, *([f'%{q[:100]}%'] * 3), country, country)).fetchall()
            settings = c.execute('SELECT company_id,destination,drive_folder_id FROM storage_settings').fetchall()
        available=[]
        for r in records:
            try:
                m.module_access(u,r['company_id'],'adjuntos')
                available.append(r)
            except HTTPException:
                pass
        setting_map={s['company_id']:s for s in settings if s['company_id'] in m.company_ids(u)}
        return m.view(req,'mg4_biblioteca.html', items=available, companies=companies,
                      countries=countries, company=company, country=country, q=q,
                      settings=setting_map)

    @app.get('/biblioteca-documental/verificar/{version_id}', response_class=HTMLResponse)
    def verificar(req:Request,version_id:int):
        u=m.require(req)
        cid=m.version_access(u,version_id)
        m.module_access(u,cid,'adjuntos')
        with m.conn() as c:
            v=c.execute('''SELECT v.id,v.revision,v.filename,v.stored_name,v.sha256,
                        a.id art_id,a.sap_code,a.master_code,co.name company_name
                        FROM versions v JOIN arts a ON a.id=v.art_id
                        JOIN companies co ON co.id=a.company_id WHERE v.id=?''',(version_id,)).fetchone()
            docs=c.execute('''SELECT att.id,att.filename,att.storage_type,att.storage_key,att.sha256,
                         d.document_type FROM mg2_version_documents d
                         JOIN attachments att ON att.id=d.attachment_id
                         WHERE d.version_id=? ORDER BY d.id''',(version_id,)).fetchall()
        # No se descargan archivos de Drive ni se exponen claves internas al navegador.
        rows=[]
        candidates=[{'name':v['filename'],'key':v['stored_name'],'hash':v['sha256'],
                     'type':'PDF principal','store':'local'}]
        candidates += [{'name':r['filename'],'key':r['storage_key'],'hash':r['sha256'],
                        'type':r['document_type'],'store':r['storage_type']} for r in docs]
        base=m.FILES.resolve()
        for item in candidates:
            result='Pendiente de verificación externa'
            if item['store']=='local':
                p=(m.FILES/item['key']).resolve()
                if p.parent!=base or not p.is_file():
                    result='Archivo no encontrado'
                else:
                    h=hashlib.sha256()
                    with p.open('rb') as f:
                        for chunk in iter(lambda:f.read(1024*1024),b''):
                            h.update(chunk)
                    result='Íntegro' if h.hexdigest()==item['hash'] else 'INTEGRIDAD NO COINCIDE'
            rows.append({'name':item['name'],'type':item['type'],'store':item['store'],'result':result})
        return m.view(req,'mg4_verificacion.html', v=v, checks=rows)
