"""Medigray: activación controlada de versiones por país (prototipo)."""
from datetime import date
from fastapi import Request, Form, HTTPException
from fastapi.responses import HTMLResponse


def register(m):
    @m.app.get('/activacion-versiones', response_class=HTMLResponse)
    def listado(req: Request, art_id: int = 0):
        u=m.require(req)
        with m.conn() as c:
            rows=c.execute("""SELECT cv.id cv_id,cv.status,cv.local_version,cv.country_id,cv.valid_from,
                                    v.id version_id,v.revision,v.change_id,a.id art_id,a.company_id,
                                    a.sap_code,a.master_code,a.product,co.name empresa,p.name pais,
                                    ch.status estado_cambio,ch.approval_type
                             FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                             JOIN arts a ON a.id=v.art_id JOIN companies co ON co.id=a.company_id
                             JOIN countries p ON p.id=cv.country_id
                             LEFT JOIN changes ch ON ch.id=v.change_id
                             WHERE a.company_id IN (SELECT company_id FROM user_companies WHERE user_id=?)
                               AND (?=0 OR a.id=?)
                             ORDER BY co.name,a.sap_code,p.name,cv.id DESC LIMIT 300""",
                             (u['id'],art_id,art_id)).fetchall()
        allowed=[]
        for r in rows:
            try:
                m.module_access(u,r['company_id'],'versiones')
                allowed.append(r)
            except HTTPException:
                pass
        return m.view(req,'mg3_activacion.html',items=allowed,can_edit=u['role'] in ('Administrador','Regulatorios'),art_id=art_id)

    @m.app.post('/activacion-versiones/{cv_id}')
    def activar(req:Request,cv_id:int,csrf_token:str=Form(...),fecha:str=Form(...),
               anterior:str=Form('Obsoleta'),fin_transicion:str=Form(''),nota:str=Form('')):
        u=m.require(req,'Administrador','Regulatorios'); m.csrf(req,csrf_token)
        cid=m.cv_access(u,cv_id);m.module_access(u,cid,'versiones')
        today=date.today().isoformat()
        try:
            activation=date.fromisoformat(fecha)
        except (ValueError,TypeError):
            raise HTTPException(400,'Fecha de activación inválida')
        if activation.isoformat()!=today:
            raise HTTPException(400,'Este procedimiento activa inmediatamente: use la fecha de hoy. La programación futura se implementará por separado.')
        if anterior not in ('Obsoleta','Transitoria'):
            raise HTTPException(400,'Destino de la versión anterior inválido')
        if anterior=='Transitoria':
            try: expiry=date.fromisoformat(fin_transicion)
            except (ValueError,TypeError): raise HTTPException(400,'Indique fin de transición válido')
            if expiry<=activation: raise HTTPException(400,'El fin de transición debe ser posterior a la activación')
        with m.conn() as c:
            c.execute('BEGIN IMMEDIATE')
            cv=c.execute("""SELECT cv.*,v.art_id,v.change_id,v.sha256,v.stored_name,
                                 ch.status estado_cambio,ch.approval_type
                          FROM country_versions cv JOIN versions v ON v.id=cv.version_id
                          LEFT JOIN changes ch ON ch.id=v.change_id WHERE cv.id=?""",(cv_id,)).fetchone()
            if not cv: raise HTTPException(404,'Versión por país no encontrada')
            if cv['status']!='Aprobado sin vigencia':
                raise HTTPException(400,'Solo se activa una versión previamente aprobada sin vigencia')
            if not cv['change_id'] or cv['estado_cambio']!='Aprobada':
                raise HTTPException(400,'La versión debe vincularse con una solicitud aprobada')
            if not cv['approval_date']:
                raise HTTPException(400,'Falta fecha de aprobación en esta versión por país')
            if not cv['sha256'] or not (m.FILES/cv['stored_name']).is_file():
                raise HTTPException(400,'No se localiza el PDF de la revisión. No se puede activar')
            previous=c.execute("""SELECT old.id,old.status,old.valid_from FROM country_versions old
                JOIN versions ov ON ov.id=old.version_id
                WHERE ov.art_id=? AND old.country_id=? AND old.status='Vigente Oficial' AND old.id<>?""",
                (cv['art_id'],cv['country_id'],cv_id)).fetchall()
            if len(previous)>1:
                raise HTTPException(409,'Hay múltiples versiones oficiales. Corrija esta inconsistencia antes de continuar')
            if len(previous)==0 and anterior=='Transitoria':
                raise HTTPException(400,'No existe una versión anterior oficial que pueda quedar transitoria')
            if previous:
                old_status='Vigente Transitorio' if anterior=='Transitoria' else 'Inactivo/Obsoleto'
                c.execute('UPDATE country_versions SET status=?,transition_until=?,notes=COALESCE(notes, ?) || ? WHERE id=?',
                          (old_status,fin_transicion if anterior=='Transitoria' else None, '',
                           '\nSustituida por versión '+str(cv_id)+' el '+today+'. '+nota[:250],previous[0]['id']))
                m.audit(c,u['id'],'DESACTIVAR_VERSION_ANTERIOR','VersionPorPais',previous[0]['id'],
                        old={'status':'Vigente Oficial'},new={'status':old_status,'replacement':cv_id})
            c.execute("UPDATE country_versions SET status='Vigente Oficial',valid_from=?,transition_until=NULL WHERE id=?",(today,cv_id))
            m.audit(c,u['id'],'ACTIVAR_VERSION','VersionPorPais',cv_id,old={'status':'Aprobado sin vigencia'},
                    new={'status':'Vigente Oficial','fecha':today,'nota':nota[:250]})
        return m.go('/activacion-versiones?art_id='+str(cv['art_id']))
