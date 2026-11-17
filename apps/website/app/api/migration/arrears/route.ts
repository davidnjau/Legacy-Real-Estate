import { NextResponse } from 'next/server';
import { djangoProxyFailure, forwardDjangoRequest } from '../../../../lib/django-proxy';

export async function GET(request: Request) {
  try {
    const djangoRes = await forwardDjangoRequest(request, '/arrears');
    if (!djangoRes.ok) {
      return djangoRes;
    }
    const data = await djangoRes.json();
    return NextResponse.json({
      assignments: (data.arrears || []).map((item: any) => ({
        id: item.invoice_id || item.id,
        unit_id: item.unit_code,
        opening_arrears: Number(item.total_due || 0),
        units: { unit_code: item.unit_code },
        users: { full_name: item.tenant_name },
        payments: [],
        arrears_editable: false,
      })),
      arrearsEditable: false,
    });
  } catch (error) {
    return djangoProxyFailure(error, 'Unable to load migration arrears.');
  }
}

export async function PATCH(request: Request) {
  return NextResponse.json(
    { error: 'Legacy arrears edits are disabled. Use audited invoice and payment workflows.' },
    { status: 410 },
  );
}
