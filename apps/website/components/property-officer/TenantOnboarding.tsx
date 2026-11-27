'use client';

import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { RefreshCw, UserPlus } from 'lucide-react';
import { usePropertyOfficerPortal } from './PortalShell';
import ExistingAccountPicker, {
  type ExistingAccount,
} from './ExistingAccountPicker';

type PropertyRecord = {
  id: number;
  branch_id: number;
  name: string;
  rent_due_day: number;
  caretakers?: { id: number; name: string; phone: string }[];
};

type UnitRecord = {
  id: number;
  property_id: number;
  code: string;
  unit_type: string;
  status: string;
  rent?: string | null;
};

type TenantDocumentRecord = {
  id: number;
  document_type: string;
  name: string;
  download_path: string;
  metadata?: Record<string, unknown>;
};

type TenantRecord = {
  id: number;
  branch_id: number;
  name: string;
  email: string | null;
  phone: string;
  portal_user_id: number | null;
  is_provisional: boolean;
  documents?: TenantDocumentRecord[];
};

type MoveInQuote = {
  unit_id: number;
  property_id: number;
  start_date: string;
  total: string;
  fingerprint: string;
  items: Array<{ name: string; amount: string; category: string }>;
};

type TenantPayload = {
  properties?: PropertyRecord[];
  units?: UnitRecord[];
  tenants?: TenantRecord[];
  tenant?: TenantRecord;
  tenancy?: {
    id: number;
    unit_id: number;
    unit_code: string;
    property_name: string;
    start_date: string;
  };
  quote?: MoveInQuote;
  existing_account_link?: boolean;
  activation_sms_queued?: boolean;
  activation_sms_error?: string | null;
  move_in_caretaker_sms_queued?: boolean | null;
  move_in_caretaker_sms_error?: string | null;
  move_in_caretaker_id?: number | null;
  document?: TenantDocumentRecord;
  error?: string;
  detail?: string;
};

const fieldClass = 'mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm';
const today = new Date().toISOString().slice(0, 10);

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const isFormData = typeof FormData !== 'undefined' && init?.body instanceof FormData;
  const response = await fetch(url, {
    ...init,
    cache: 'no-store',
    headers: {
      ...(init?.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null) as TenantPayload | null;
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.error || `Request failed with status ${response.status}.`);
  }
  if (!payload) throw new Error('The server returned an empty response.');
  return payload as T;
}

export default function TenantOnboarding() {
  const { agency, branchIds } = usePropertyOfficerPortal();
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [units, setUnits] = useState<UnitRecord[]>([]);
  const [tenants, setTenants] = useState<TenantRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  const [tenantType, setTenantType] = useState<'individual' | 'company'>('individual');
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [identity, setIdentity] = useState('');
  const [companyRegistrationNumber, setCompanyRegistrationNumber] = useState('');
  const [contactPerson, setContactPerson] = useState('');
  const [kraPin, setKraPin] = useState('');
  const [occupation, setOccupation] = useState('');
  const [employer, setEmployer] = useState('');
  const [nextOfKinName, setNextOfKinName] = useState('');
  const [nextOfKinPhone, setNextOfKinPhone] = useState('');
  const [nextOfKinRelationship, setNextOfKinRelationship] = useState('');
  const [propertyId, setPropertyId] = useState<number | ''>('');
  const [unitId, setUnitId] = useState<number | ''>('');
  const [caretakerId, setCaretakerId] = useState<number | ''>('');
  const [startDate, setStartDate] = useState(today);
  const [endDate, setEndDate] = useState('');
  const [dueDay, setDueDay] = useState(10);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [paymentChannel, setPaymentChannel] = useState('mpesa');
  const [transactionReference, setTransactionReference] = useState('');
  const [paymentDate, setPaymentDate] = useState(today);
  const [paymentAmount, setPaymentAmount] = useState('');
  const [quote, setQuote] = useState<MoveInQuote | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);

  const [createdTenant, setCreatedTenant] = useState<TenantRecord | null>(null);
  const [createdTenancy, setCreatedTenancy] = useState<TenantPayload['tenancy'] | null>(null);
  const [activationSmsQueued, setActivationSmsQueued] = useState(false);
  const [activationSmsError, setActivationSmsError] = useState<string | null>(null);
  const [moveInCaretakerSmsQueued, setMoveInCaretakerSmsQueued] = useState<boolean | null>(null);
  const [moveInCaretakerSmsError, setMoveInCaretakerSmsError] = useState<string | null>(null);
  const [documentType, setDocumentType] = useState('signed_agreement');
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [identityDocumentFile, setIdentityDocumentFile] = useState<File | null>(null);
  const [uploadedDocuments, setUploadedDocuments] = useState<
    NonNullable<TenantPayload['document']>[]
  >([]);
  const visibleProperties = useMemo(
    () => properties.filter((property) => branchIds.has(property.branch_id)),
    [branchIds, properties],
  );
  const propertyById = useMemo(
    () => new Map(visibleProperties.map((property) => [property.id, property])),
    [visibleProperties],
  );
  const visibleUnits = useMemo(
    () => units.filter((unit) => unit.status === 'vacant' && propertyById.has(unit.property_id)),
    [propertyById, units],
  );
  const availableUnits = useMemo(
    () => visibleUnits.filter((unit) => unit.property_id === propertyId),
    [propertyId, visibleUnits],
  );
  const availableCaretakers = propertyId === ''
    ? []
    : propertyById.get(propertyId)?.caretakers ?? [];
  const visibleTenants = useMemo(
    () => tenants.filter((tenant) => branchIds.has(tenant.branch_id)),
    [branchIds, tenants],
  );

  const load = useCallback(async () => {
    if (!agency) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const [propertyPayload, unitPayload, tenantPayload] = await Promise.all([
        requestJson<{ properties?: PropertyRecord[] }>('/api/property-officer/properties'),
        requestJson<{ units?: UnitRecord[] }>('/api/property-officer/units'),
        requestJson<{ tenants?: TenantRecord[] }>('/api/property-officer/tenants'),
      ]);
      setProperties(propertyPayload.properties ?? []);
      setUnits(unitPayload.units ?? []);
      setTenants(tenantPayload.tenants ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load tenant onboarding records.');
    } finally {
      setLoading(false);
    }
  }, [agency]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  useEffect(() => {
    if (propertyId === '' || !visibleProperties.some((property) => property.id === propertyId)) {
      setPropertyId(visibleProperties[0]?.id ?? '');
    }
  }, [propertyId, visibleProperties]);

  useEffect(() => {
    if (!availableUnits.some((unit) => unit.id === unitId)) {
      setUnitId(availableUnits[0]?.id ?? '');
    }
  }, [availableUnits, unitId]);

  useEffect(() => {
    if (!availableCaretakers.some((caretaker) => caretaker.id === caretakerId)) {
      setCaretakerId(availableCaretakers[0]?.id ?? '');
    }
  }, [availableCaretakers, caretakerId]);

  useEffect(() => {
    const selectedUnit = availableUnits.find((item) => item.id === unitId);
    const selectedProperty = selectedUnit ? propertyById.get(selectedUnit.property_id) : undefined;
    if (selectedProperty) setDueDay(selectedProperty.rent_due_day);
  }, [availableUnits, propertyById, unitId]);

  useEffect(() => {
    let active = true;
    if (createdTenancy || unitId === '' || !startDate) {
      if (!createdTenancy) setQuote(null);
      return () => {
        active = false;
      };
    }
    setQuote(null);
    setQuoteLoading(true);
    const query = new URLSearchParams({
      unit_id: String(unitId),
      start_date: startDate,
    });
    requestJson<{ quote: MoveInQuote }>(
      `/api/property-officer/move-in-quote?${query.toString()}`,
    ).then((result) => {
      if (active) setQuote(result.quote);
    }).catch((cause: unknown) => {
      if (active) {
        setError(cause instanceof Error ? cause.message : 'Unable to calculate the move-in amount.');
      }
    }).finally(() => {
      if (active) setQuoteLoading(false);
    });
    return () => {
      active = false;
    };
  }, [createdTenancy, startDate, unitId]);

  useEffect(() => {
    if (quote && !createdTenancy) setPaymentAmount(quote.total);
  }, [createdTenancy, quote]);

  async function completeOnboarding(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const selectedUnitId = createdTenancy?.unit_id ?? unitId;
    if (selectedUnitId === '' || caretakerId === '' || !quote || !confirmPaid) return;
    if (!identityDocumentFile) {
      setError("Upload the tenant's national ID or passport before completing onboarding.");
      return;
    }
    const unit = availableUnits.find((item) => item.id === selectedUnitId);
    const property = unit ? propertyById.get(unit.property_id) : undefined;
    if ((!unit || !property) && !createdTenancy) {
      setError('Select a vacant unit in one of your assigned branches.');
      return;
    }

    setSaving(true);
    setError('');
    setNotice('');
    try {
      const formData = new FormData();
      const onboardingFields: Record<string, string | number | boolean> = {
        unit_id: selectedUnitId,
        caretaker_id: caretakerId,
        start_date: startDate,
        end_date: endDate,
        tenancy_type: endDate ? 'fixed_term' : 'month_to_month',
        due_day: dueDay,
        tenant_type: tenantType,
        existing_user_id: selectedAccountId ?? '',
        name,
        email,
        phone,
        national_id_or_passport: identity,
        company_registration_number: tenantType === 'company' ? companyRegistrationNumber : '',
        contact_person: tenantType === 'company' ? contactPerson : '',
        kra_pin: kraPin,
        occupation,
        employer,
        next_of_kin_name: nextOfKinName,
        next_of_kin_phone: nextOfKinPhone,
        next_of_kin_relationship: nextOfKinRelationship,
        confirm_paid: confirmPaid,
        payment_channel: paymentChannel,
        transaction_reference: transactionReference,
        payment_date: paymentDate,
        payment_amount: paymentAmount,
        quote_total: quote.total,
        quote_fingerprint: quote.fingerprint,
      };
      Object.entries(onboardingFields).forEach(([key, value]) => {
        formData.set(key, String(value));
      });
      formData.set('identity_document', identityDocumentFile);
      const result = await requestJson<TenantPayload>('/api/property-officer/tenant-onboarding', {
        method: 'POST',
        body: formData,
      });
      const tenant = result.tenant;
      const tenancy = result.tenancy;
      if (!tenant || !tenancy) {
        throw new Error('The server did not return the completed tenant and tenancy records.');
      }
      setCreatedTenant(tenant);
      setCreatedTenancy(tenancy);
      setUploadedDocuments(tenant.documents ?? []);
      setIdentityDocumentFile(null);
      setActivationSmsQueued(Boolean(result.activation_sms_queued));
      setActivationSmsError(result.activation_sms_error ?? null);
      setMoveInCaretakerSmsQueued(result.move_in_caretaker_sms_queued ?? null);
      setMoveInCaretakerSmsError(result.move_in_caretaker_sms_error ?? null);
      setTenants((current) => [
        tenant,
        ...current.filter((record) => record.id !== tenant.id),
      ]);
      setNotice(
        result.existing_account_link
          ? 'Existing agency account linked to the tenancy.'
          : result.activation_sms_queued
          ? 'Payment confirmation and tenant onboarding are complete. The activation code was sent by SMS.'
          : 'Tenant onboarding is complete, but one or more activation messages need to be retried.',
      );
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to complete tenant onboarding.');
    } finally {
      setSaving(false);
    }
  }

  function startAnotherOnboarding() {
    setCreatedTenant(null);
    setCreatedTenancy(null);
    setConfirmPaid(false);
    setQuote(null);
    setUploadedDocuments([]);
    setDocumentFile(null);
    setIdentityDocumentFile(null);
    setActivationSmsQueued(false);
    setActivationSmsError(null);
    setMoveInCaretakerSmsQueued(null);
    setMoveInCaretakerSmsError(null);
    setTenantType('individual');
    setSelectedAccountId(null);
    setName('');
    setEmail('');
    setPhone('');
    setIdentity('');
    setCompanyRegistrationNumber('');
    setContactPerson('');
    setKraPin('');
    setOccupation('');
    setEmployer('');
    setNextOfKinName('');
    setNextOfKinPhone('');
    setNextOfKinRelationship('');
    setPropertyId(visibleProperties[0]?.id ?? '');
    setUnitId('');
    setStartDate(today);
    setEndDate('');
    setPaymentChannel('mpesa');
    setTransactionReference('');
    setPaymentDate(today);
    setPaymentAmount('');
    setNotice('');
    setError('');
  }

  async function uploadDocument() {
    if (!createdTenant || !createdTenancy || !documentFile) return;
    setSaving(true);
    setError('');
    const formData = new FormData();
    formData.set('document_type', documentType);
    formData.set('name', documentFile.name);
    formData.set('tenancy_id', String(createdTenancy.id));
    formData.set('file', documentFile);
    try {
      const result = await requestJson<TenantPayload>(
        `/api/property-officer/tenants/${createdTenant.id}/documents`,
        { method: 'POST', body: formData },
      );
      const uploadedDocument = result.document;
      if (!uploadedDocument) throw new Error('The saved document was not returned by the server.');
      setUploadedDocuments((documents) => [...documents, uploadedDocument]);
      setCreatedTenant((current) => current
        ? { ...current, documents: [...(current.documents ?? []), uploadedDocument] }
        : current);
      setTenants((current) => current.map((tenant) => (
        tenant.id === createdTenant.id
          ? { ...tenant, documents: [...(tenant.documents ?? []), uploadedDocument] }
          : tenant
      )));
      setDocumentFile(null);
      setNotice(`${uploadedDocument.name} uploaded to the tenant's private file record.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to upload the tenant document.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-emerald-800">Property operations</p>
          <h1 className="mt-1 text-3xl font-bold text-slate-950">Tenant onboarding</h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Review the property-specific move-in amount, confirm payment, then create the tenancy and send activation instructions.
          </p>
        </div>
        <button onClick={() => setReloadKey((value) => value + 1)} disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold disabled:opacity-50">
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </header>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">{notice}</p>}

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <form onSubmit={completeOnboarding} className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-50 text-emerald-800"><UserPlus size={19} /></span>
              <div>
                <h2 className="font-bold">Tenant and move-in details</h2>
                <p className="text-xs text-slate-500">Branch and unit access is restricted to your assignment.</p>
              </div>
            </div>
            {(createdTenant || createdTenancy) && (
              <button type="button" onClick={startAnotherOnboarding}
                className="shrink-0 rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">
                Start another
              </button>
            )}
          </div>

          <fieldset disabled={Boolean(createdTenancy) || saving} className="space-y-4 disabled:opacity-70">
            <label className="block text-sm font-semibold">Tenant type
              <select value={tenantType} onChange={(event) => {
                setTenantType(event.target.value as 'individual' | 'company');
                setSelectedAccountId(null);
              }}
                className={fieldClass}>
                <option value="individual">Individual</option>
                <option value="company">Company</option>
              </select>
            </label>
            {agency && (
              <ExistingAccountPicker
                agencyId={agency.id}
                selectedId={selectedAccountId}
                onSelect={(account: ExistingAccount | null) => {
                  setSelectedAccountId(account?.id ?? null);
                  if (account) {
                    if (tenantType === 'company') setContactPerson(account.name);
                    else setName(account.name);
                    setEmail(account.email);
                    setPhone(account.phone);
                  }
                }}
              />
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-semibold">Full name / company name
                <input required value={name} onChange={(event) => setName(event.target.value)}
                  readOnly={selectedAccountId !== null && tenantType === 'individual'}
                  className={fieldClass} />
              </label>
              <label className="block text-sm font-semibold">Email
                <input required type="email" autoComplete="email" value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  readOnly={selectedAccountId !== null} className={fieldClass} />
              </label>
            </div>
            <label className="block text-sm font-semibold">Move-in inspection caretaker
              <select required disabled={Boolean(createdTenancy) || saving} value={caretakerId}
                onChange={(event) => setCaretakerId(Number(event.target.value) || '')}
                className={fieldClass}>
                <option value="" disabled>Select an assigned caretaker</option>
                {availableCaretakers.map((caretaker) => (
                  <option key={caretaker.id} value={caretaker.id}>
                    {caretaker.name}{caretaker.phone ? '' : ' · phone required'}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-xs font-normal text-slate-500">
                The inspection task will appear as pending immediately. SMS is queued to this caretaker, and the on-site checklist can be submitted from the move-in date.
              </span>
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-semibold">Phone
                <input required type="tel" autoComplete="tel" placeholder="07XX XXX XXX" value={phone}
                  onChange={(event) => setPhone(event.target.value)}
                  readOnly={selectedAccountId !== null} className={fieldClass} />
              </label>
              <label className="block text-sm font-semibold">
                {tenantType === 'individual' ? 'National ID or passport number' : "Contact person's national ID or passport number"}
                <input required value={identity} onChange={(event) => setIdentity(event.target.value)} className={fieldClass} />
              </label>
            </div>
            {tenantType === 'company' && (
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block text-sm font-semibold">Company registration number
                  <input required value={companyRegistrationNumber} onChange={(event) => setCompanyRegistrationNumber(event.target.value)} className={fieldClass} />
                </label>
                <label className="block text-sm font-semibold">Contact person
                  <input required value={contactPerson} onChange={(event) => setContactPerson(event.target.value)}
                    readOnly={selectedAccountId !== null} className={fieldClass} />
                </label>
              </div>
            )}
            <label className="block text-sm font-semibold">
              {tenantType === 'individual' ? 'National ID or passport document' : "Contact person's national ID or passport document"}
              <input required type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp"
                onChange={(event) => setIdentityDocumentFile(event.target.files?.[0] ?? null)} className={fieldClass} />
              <span className="mt-1 block text-xs font-normal text-slate-500">PDF, JPEG, PNG, or WebP; maximum 10 MB. This is stored privately with the tenancy.</span>
            </label>
            <label className="block text-sm font-semibold">KRA PIN <span className="font-normal text-slate-500">optional</span>
              <input value={kraPin} onChange={(event) => setKraPin(event.target.value)} className={fieldClass} />
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-semibold">Occupation <span className="font-normal text-slate-500">optional</span>
                <input maxLength={150} value={occupation} onChange={(event) => setOccupation(event.target.value)} className={fieldClass} />
              </label>
              <label className="block text-sm font-semibold">Employer <span className="font-normal text-slate-500">optional</span>
                <input maxLength={255} value={employer} onChange={(event) => setEmployer(event.target.value)} className={fieldClass} />
              </label>
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <label className="block text-sm font-semibold">Next-of-kin name <span className="font-normal text-slate-500">optional</span>
                <input maxLength={255} value={nextOfKinName} onChange={(event) => setNextOfKinName(event.target.value)} className={fieldClass} />
              </label>
              <label className="block text-sm font-semibold">Next-of-kin phone <span className="font-normal text-slate-500">optional</span>
                <input type="tel" placeholder="07XX XXX XXX" value={nextOfKinPhone} onChange={(event) => setNextOfKinPhone(event.target.value)} className={fieldClass} />
              </label>
              <label className="block text-sm font-semibold">Relationship <span className="font-normal text-slate-500">optional</span>
                <input maxLength={100} value={nextOfKinRelationship} onChange={(event) => setNextOfKinRelationship(event.target.value)} className={fieldClass} />
              </label>
            </div>
          </fieldset>

          {createdTenant && (
            <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
              Tenant record #{createdTenant.id} is saved. If the tenancy step failed, submit again to resume without creating a duplicate tenant.
            </p>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-semibold">Property
              <select required disabled={Boolean(createdTenancy) || saving} value={propertyId}
                onChange={(event) => {
                  setPropertyId(Number(event.target.value) || '');
                  setUnitId('');
                  setConfirmPaid(false);
                }} className={fieldClass}>
                <option value="" disabled>Select property</option>
                {visibleProperties.map((property) => (
                  <option key={property.id} value={property.id}>{property.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-semibold">Vacant unit
              <select required disabled={Boolean(createdTenancy) || saving} value={unitId}
                onChange={(event) => {
                  setUnitId(Number(event.target.value) || '');
                  setConfirmPaid(false);
                }} className={fieldClass}>
                <option value="" disabled>Select unit</option>
                {availableUnits.map((item) => (
                  <option key={item.id} value={item.id}>{item.code} · {item.unit_type}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-semibold">Rent due day
              <input type="number" min={1} max={31} required disabled={Boolean(createdTenancy) || saving}
                value={dueDay} onChange={(event) => setDueDay(Number(event.target.value))}
                className={fieldClass} />
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-semibold">Tenancy start date
              <input type="date" required disabled={Boolean(createdTenancy) || saving} value={startDate}
                onChange={(event) => {
                  setStartDate(event.target.value);
                  setConfirmPaid(false);
                }} className={fieldClass} />
            </label>
            <label className="block text-sm font-semibold">End date <span className="font-normal text-slate-500">optional</span>
              <input type="date" disabled={Boolean(createdTenancy) || saving} min={startDate} value={endDate}
                onChange={(event) => setEndDate(event.target.value)} className={fieldClass} />
            </label>
          </div>
          {quoteLoading && <p className="text-sm text-slate-500">Calculating the move-in amount…</p>}
          {quote && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
              <h3 className="text-sm font-bold text-emerald-950">Move-in amount · KES {Number(quote.total).toLocaleString()}</h3>
              <ul className="mt-2 space-y-1 text-sm text-emerald-900">
                {quote.items.map((item) => (
                  <li key={`${item.category}-${item.name}`} className="flex justify-between gap-3">
                    <span>{item.name}</span>
                    <span>KES {Number(item.amount).toLocaleString()}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-emerald-900">
                This upfront amount includes prorated rent through month-end, the security deposit, and configured utility deposits only. Recurring charges and metered usage are billed separately.
              </p>
            </div>
          )}
          {quote && !createdTenancy && (
            <fieldset disabled={saving} className="grid gap-4 rounded-xl border border-slate-200 p-4 sm:grid-cols-2 disabled:opacity-70">
              <legend className="px-1 text-sm font-bold">Record move-in payment</legend>
              <label className="block text-sm font-semibold">Payment channel
                <select required value={paymentChannel} onChange={(event) => setPaymentChannel(event.target.value)} className={fieldClass}>
                  <option value="mpesa">M-Pesa</option>
                  <option value="bank_transfer">Bank transfer</option>
                  <option value="cash">Cash</option>
                </select>
              </label>
              <label className="block text-sm font-semibold">Transaction / office receipt reference
                <input required maxLength={100} value={transactionReference}
                  onChange={(event) => setTransactionReference(event.target.value)} className={fieldClass} />
              </label>
              <label className="block text-sm font-semibold">Payment date
                <input required type="date" max={today} value={paymentDate}
                  onChange={(event) => setPaymentDate(event.target.value)} className={fieldClass} />
              </label>
              <label className="block text-sm font-semibold">Amount received (KES)
                <input required type="number" min="0.01" step="0.01" value={paymentAmount}
                  onChange={(event) => setPaymentAmount(event.target.value)} className={fieldClass} />
              </label>
            </fieldset>
          )}
          {quote && !createdTenancy && (
            <label className="flex items-start gap-3 rounded-xl border border-slate-200 p-4 text-sm">
              <input type="checkbox" required checked={confirmPaid}
                onChange={(event) => setConfirmPaid(event.target.checked)}
                className="mt-0.5 h-4 w-4 accent-emerald-800" />
              <span>I confirm that the tenant has paid the full move-in amount of KES {Number(quote.total).toLocaleString()}.</span>
            </label>
          )}
          {createdTenancy && (
            <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
              Tenant {createdTenant?.name} and tenancy #{createdTenancy.id} are onboarded for {createdTenancy.unit_code}.
            </p>
          )}
          <button type="submit" disabled={saving || loading || quoteLoading || (!createdTenancy && (
            unitId === '' || caretakerId === '' || !quote || !confirmPaid || !transactionReference.trim()
            || !paymentDate || !paymentAmount
          ))}
            className="w-full rounded-xl bg-emerald-800 px-4 py-3 font-bold text-white disabled:opacity-50">
            {saving ? 'Saving onboarding…' : createdTenancy ? 'Retry activation messages' : 'Confirm payment and onboard tenant'}
          </button>
          {!visibleProperties.length && !loading && (
            <p className="text-sm text-amber-800">There are no properties in your assigned branches.</p>
          )}
          {propertyId !== '' && !availableUnits.length && !loading && !createdTenancy && (
            <p className="text-sm text-amber-800">There are no vacant units in your assigned branches.</p>
          )}
        </form>

        <aside className="space-y-5">
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="font-bold">Activation delivery</h2>
            <p className="mt-2 text-sm text-slate-600">
              After the full move-in balance is paid, the tenant receives a passwordless activation code by SMS.
            </p>
            {createdTenancy && (
              <div className="mt-4 space-y-2 text-sm">
                <p className={activationSmsQueued ? 'text-emerald-800' : 'text-red-700'}>
                  SMS: {activationSmsQueued ? 'queued for delivery' : `not queued${activationSmsError ? ` — ${activationSmsError}` : ''}`}
                </p>
                <p className={moveInCaretakerSmsQueued ? 'text-emerald-800' : 'text-red-700'}>
                  Move-in inspection SMS: {moveInCaretakerSmsQueued
                    ? 'queued for the assigned caretaker'
                    : `not queued${moveInCaretakerSmsError ? ` — ${moveInCaretakerSmsError}` : ''}`}
                </p>
              </div>
            )}
          </section>

          {createdTenant && createdTenancy && (
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="font-bold">Tenancy documents</h2>
              <p className="mt-1 text-sm text-slate-600">
                The national ID or passport is included from onboarding. Upload signed agreements or other additional move-in records here.
              </p>
              <label className="mt-4 block text-sm font-semibold">Document type
                <select value={documentType} onChange={(event) => setDocumentType(event.target.value)} className={fieldClass}>
                  <option value="signed_agreement">Signed tenancy agreement</option>
                  <option value="identity_document">Identity document</option>
                  <option value="proof_of_payment">Proof of payment</option>
                  <option value="other">Other</option>
                </select>
              </label>
              <label className="mt-3 block text-sm font-semibold">Document file
                <input type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp"
                  onChange={(event) => setDocumentFile(event.target.files?.[0] ?? null)} className={fieldClass} />
              </label>
              <button type="button" onClick={() => void uploadDocument()} disabled={saving || !documentFile}
                className="mt-3 rounded-lg border border-emerald-800 px-3 py-2 text-sm font-semibold text-emerald-900 disabled:opacity-50">
                {saving ? 'Uploading…' : 'Upload document'}
              </button>
              {uploadedDocuments.length > 0 && (
                <ul className="mt-4 divide-y divide-slate-100 text-sm">
                  {uploadedDocuments.map((document) => (
                    <li key={document.id} className="py-2">
                      <a className="font-semibold text-emerald-900 underline" href={`/api/property-officer/tenants/${createdTenant.id}/documents/${document.id}`}>
                        {document.name}
                      </a>
                      <span className="ml-2 text-xs text-slate-500">{document.document_type.replace('_', ' ')}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="font-bold">Tenant records</h2>
            <p className="mt-1 text-xs text-slate-500">{visibleTenants.length} tenants in your assigned branches</p>
            <ul className="mt-3 divide-y divide-slate-100">
              {visibleTenants.slice(0, 8).map((tenant) => (
                <li key={tenant.id} className="flex items-center justify-between gap-3 py-3 text-sm">
                  <span>
                    <span className="block font-semibold">{tenant.name}</span>
                    <span className="block text-xs text-slate-500">{tenant.email || tenant.phone}</span>
                    {tenant.documents?.length ? (
                      <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                        {tenant.documents.map((document) => (
                          <a
                            key={document.id}
                            className="text-xs font-semibold text-emerald-900 underline"
                            href={`/api/property-officer/tenants/${tenant.id}/documents/${document.id}`}
                          >
                            {document.name}
                          </a>
                        ))}
                      </span>
                    ) : null}
                  </span>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${tenant.portal_user_id ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>
                    {tenant.portal_user_id ? 'Portal active' : 'Setup pending'}
                  </span>
                </li>
              ))}
              {!loading && visibleTenants.length === 0 && (
                <li className="py-5 text-center text-sm text-slate-500">No tenant records yet.</li>
              )}
            </ul>
          </section>
        </aside>
      </section>
    </main>
  );
}
