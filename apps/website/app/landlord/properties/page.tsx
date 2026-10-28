'use client';

import { useEffect, useState } from 'react';
import { Building2, Plus, Save, Check, Trash2 } from 'lucide-react';

type RecurringCharge = { id?: string; name: string; amount: string | number; is_active?: boolean };
type Property = {
  id: string;
  name: string;
  address: string | null;
  currency_code: string;
  timezone: string;
  default_rent: number;
  security_deposit: number;
  water_deposit: number;
  default_garbage_charge: number;
  default_water_rate: number;
  rent_due_day: number;
  billing_mode: 'metered_water' | 'fixed_utilities';
  recurringCharges: RecurringCharge[];
};

type PropertyForm = Omit<Property, 'id' | 'recurringCharges'> & { recurringCharges: RecurringCharge[] };

const emptyForm: PropertyForm = {
  name: '', address: '', currency_code: 'KES', timezone: 'Africa/Nairobi',
  default_rent: 0, security_deposit: 0, water_deposit: 0,
  default_garbage_charge: 0, default_water_rate: 0, rent_due_day: 10,
  billing_mode: 'metered_water', recurringCharges: [],
};

export default function PropertiesPage() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [activePropertyId, setActivePropertyId] = useState('');
  const [form, setForm] = useState<PropertyForm>(emptyForm);
  const [isCreating, setIsCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadProperties = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/landlord/properties');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to load properties.');
      const items: Property[] = data.properties || [];
      setProperties(items);
      const selected = data.activePropertyId || items[0]?.id || '';
      setActivePropertyId(selected);
      const active = items.find((property) => property.id === selected);
      setForm(active ? formFromProperty(active) : emptyForm);
      setIsCreating(false);
    } catch (loadError: any) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadProperties(); }, []);

  const chooseProperty = async (propertyId: string) => {
    setError('');
    const response = await fetch('/api/landlord/properties', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activePropertyId: propertyId }),
    });
    const data = await response.json();
    if (!response.ok) { setError(data.error || 'Unable to switch property.'); return; }
    setActivePropertyId(propertyId);
    const selected = properties.find((property) => property.id === propertyId);
    if (selected) setForm(formFromProperty(selected));
    setIsCreating(false);
    setNotice('Active property changed.');
  };

  const addProperty = () => {
    setForm(emptyForm);
    setIsCreating(true);
    setError('');
    setNotice('');
  };

  const updateField = (key: keyof PropertyForm, value: string | number) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const addRecurringCharge = () => setForm((current) => ({
    ...current,
    recurringCharges: [...current.recurringCharges, { name: '', amount: 0, is_active: true }],
  }));

  const updateRecurringCharge = (index: number, key: 'name' | 'amount', value: string | number) => {
    setForm((current) => ({
      ...current,
      recurringCharges: current.recurringCharges.map((charge, chargeIndex) => chargeIndex === index ? { ...charge, [key]: value } : charge),
    }));
  };

  const removeRecurringCharge = (index: number) => setForm((current) => ({
    ...current,
    recurringCharges: current.recurringCharges.filter((_, chargeIndex) => chargeIndex !== index),
  }));

  const saveProperty = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    setNotice('');
    const payload = {
      name: form.name,
      address: form.address,
      currencyCode: form.currency_code,
      timezone: form.timezone,
      defaultRent: Number(form.default_rent),
      securityDeposit: Number(form.security_deposit),
      waterDeposit: Number(form.water_deposit),
      garbageCharge: Number(form.default_garbage_charge),
      waterRate: Number(form.default_water_rate),
      rentDueDay: Number(form.rent_due_day),
      billingMode: form.billing_mode,
      recurringCharges: form.recurringCharges.map((charge) => ({ name: charge.name, amount: Number(charge.amount), isActive: charge.is_active !== false })),
    };

    try {
      const response = await fetch('/api/landlord/properties', {
        method: isCreating ? 'POST' : 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(isCreating ? payload : { ...payload, propertyId: activePropertyId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to save property.');
      const propertyId = data.property?.id || activePropertyId;
      if (isCreating && propertyId) {
        const activateResponse = await fetch('/api/landlord/properties', {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ activePropertyId: propertyId }),
        });
        const activateData = await activateResponse.json();
        if (!activateResponse.ok) throw new Error(activateData.error || 'Property saved but could not be selected.');
      }
      await loadProperties();
      setNotice(isCreating ? 'Property created and selected.' : 'Property settings saved.');
    } catch (saveError: any) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="p-8 text-sm text-slate-500">Loading properties…</div>;

  return (
    <div className="max-w-5xl space-y-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase font-bold text-emerald-700">Workspace</p>
          <h1 className="mt-1 text-3xl font-bold text-slate-900">Properties</h1>
          <p className="mt-1 text-sm text-slate-500">Set the defaults and billing behavior for each rental property.</p>
        </div>
        <button type="button" onClick={addProperty} className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800"><Plus size={17} /> Add property</button>
      </header>

      {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {notice && <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</div>}

      <div className="flex flex-wrap gap-2 border-b border-slate-200">
        {properties.map((property) => <button key={property.id} type="button" onClick={() => chooseProperty(property.id)} className={`border-b-2 px-3 py-2 text-sm font-semibold ${activePropertyId === property.id && !isCreating ? 'border-emerald-600 text-emerald-800' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
          {property.name}{activePropertyId === property.id && !isCreating ? ' · Active' : ''}
        </button>)}
        {isCreating && <span className="border-b-2 border-emerald-600 px-3 py-2 text-sm font-semibold text-emerald-800">New property</span>}
        {properties.length === 0 && !isCreating && <p className="py-3 text-sm text-slate-500">No properties yet. Add one to start configuring your workspace.</p>}
      </div>

      {(activePropertyId || isCreating) && (
        <form onSubmit={saveProperty} className="space-y-6">
          <section className="grid gap-4 sm:grid-cols-2 rounded-xl border border-slate-200 bg-white p-5">
            <Field label="Property name"><input required maxLength={120} value={form.name} onChange={(event) => updateField('name', event.target.value)} className={inputClass} placeholder="e.g. Riverside Court" /></Field>
            <Field label="Address"><input value={form.address || ''} onChange={(event) => updateField('address', event.target.value)} className={inputClass} /></Field>
            <Field label="Currency"><input required maxLength={3} value={form.currency_code} onChange={(event) => updateField('currency_code', event.target.value.toUpperCase())} className={inputClass} /></Field>
            <Field label="Timezone"><input required value={form.timezone} onChange={(event) => updateField('timezone', event.target.value)} className={inputClass} /></Field>
          </section>

          <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
            <div><h2 className="font-semibold text-slate-900">Default charges</h2><p className="text-xs text-slate-500 mt-1">Use these values as defaults when adding units; each unit can have its own amount.</p></div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <NumberField label="Monthly rent" value={form.default_rent} onChange={(value) => updateField('default_rent', value)} currency={form.currency_code} />
              <NumberField label="Security deposit" value={form.security_deposit} onChange={(value) => updateField('security_deposit', value)} currency={form.currency_code} />
              <NumberField label="Water deposit" value={form.water_deposit} onChange={(value) => updateField('water_deposit', value)} currency={form.currency_code} />
              <NumberField label="Garbage charge" value={form.default_garbage_charge} onChange={(value) => updateField('default_garbage_charge', value)} currency={form.currency_code} />
              <NumberField label="Water rate per unit" value={form.default_water_rate} onChange={(value) => updateField('default_water_rate', value)} currency={form.currency_code} />
              <Field label="Rent due day"><input type="number" min={1} max={28} value={form.rent_due_day} onChange={(event) => updateField('rent_due_day', Number(event.target.value))} className={inputClass} /></Field>
            </div>
          </section>

          <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5">
            <div><h2 className="font-semibold text-slate-900">Water billing</h2><p className="text-xs text-slate-500 mt-1">Choose whether invoices wait for meter readings or use a fixed monthly charge.</p></div>
            <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1">
              <button type="button" onClick={() => updateField('billing_mode', 'metered_water')} className={`rounded-md px-3 py-2 text-sm font-semibold ${form.billing_mode === 'metered_water' ? 'bg-white text-emerald-800 shadow-sm' : 'text-slate-600'}`}>Metered water</button>
              <button type="button" onClick={() => updateField('billing_mode', 'fixed_utilities')} className={`rounded-md px-3 py-2 text-sm font-semibold ${form.billing_mode === 'fixed_utilities' ? 'bg-white text-emerald-800 shadow-sm' : 'text-slate-600'}`}>Fixed utilities</button>
            </div>
            {form.recurringCharges.length > 0 && <div className="space-y-2">{form.recurringCharges.map((charge, index) => <div key={charge.id || index} className="grid grid-cols-[1fr_9rem_auto] gap-2">
              <input aria-label="Recurring utility name" value={charge.name} onChange={(event) => updateRecurringCharge(index, 'name', event.target.value)} className={inputClass} placeholder="e.g. Internet" />
              <input aria-label="Recurring utility amount" type="number" min={0} value={charge.amount} onChange={(event) => updateRecurringCharge(index, 'amount', event.target.value)} className={inputClass} />
              <button type="button" onClick={() => removeRecurringCharge(index)} className="p-2 text-slate-500 hover:text-red-700" title="Remove charge"><Trash2 size={17} /></button>
            </div>)}</div>}
            <button type="button" onClick={addRecurringCharge} className="text-sm font-semibold text-emerald-700 hover:text-emerald-900">+ Add recurring utility</button>
          </section>

          <div className="flex justify-end"><button type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-lg bg-emerald-700 px-5 py-2.5 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-50"><Save size={16} />{saving ? 'Saving…' : isCreating ? 'Create property' : 'Save property settings'}</button></div>
        </form>
      )}
    </div>
  );
}

const inputClass = 'mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-sm font-medium text-slate-700">{label}{children}</label>;
}

function NumberField({ label, value, onChange, currency }: { label: string; value: number; onChange: (value: number) => void; currency: string }) {
  return <Field label={`${label} (${currency})`}><input type="number" min={0} step="0.01" value={value} onChange={(event) => onChange(Number(event.target.value))} className={inputClass} /></Field>;
}

function formFromProperty(property: Property): PropertyForm {
  return {
    name: property.name,
    address: property.address,
    currency_code: property.currency_code,
    timezone: property.timezone,
    default_rent: property.default_rent,
    security_deposit: property.security_deposit,
    water_deposit: property.water_deposit,
    default_garbage_charge: property.default_garbage_charge,
    default_water_rate: property.default_water_rate,
    rent_due_day: property.rent_due_day,
    billing_mode: property.billing_mode,
    recurringCharges: (property.recurringCharges || []).map((charge) => ({ ...charge, amount: Number(charge.amount) })),
  };
}