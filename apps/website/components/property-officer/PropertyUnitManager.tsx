'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Building2, Plus, RefreshCw } from 'lucide-react';
import { usePropertyOfficerPortal } from './PortalShell';
import { PortalLoading, PortalPagination, usePortalPagination } from './PortalUI';

type Branch = NonNullable<NonNullable<ReturnType<typeof usePropertyOfficerPortal>['agency']>['branches']>[number];

type PropertyRecord = {
  id: number;
  branch_id: number;
  landlord_id: number | null;
  name: string;
  property_type: string;
  location: string;
  address: string | null;
  rent_due_day: number;
  billing_mode: string;
  expected_unit_count: number;
  default_unit_type: string;
  default_rent: string;
  security_deposit: string;
  move_in_letting_fee: string;
  water_deposit: string;
  default_garbage_charge: string;
  default_water_rate: string;
  amenities: unknown;
  house_rules: unknown;
  default_handover_items: { name: string; quantity: number }[];
  unit_types?: UnitTypeRecord[];
};

type UnitTypeRecord = {
  id: number;
  name: string;
  unit_count: number;
  monthly_rent: string;
  security_deposit: string;
  utilities: UnitTypeUtilityRecord[];
};

type UnitTypeUtilityRecord = {
  id: number;
  utility_type: string;
  name: string;
  billing_method: string;
  fixed_amount: string | null;
  rate_per_unit: string | null;
  deposit_amount: string | null;
};

type UnitTypeDraft = {
  name: string;
  unitCount: string;
  monthlyRent: string;
  securityDeposit: string;
  utilities: UnitTypeUtilityDraft[];
};

type UnitTypeUtilityDraft = {
  utilityType: string;
  name: string;
  billingMethod: string;
  fixedAmount: string;
  ratePerUnit: string;
  depositAmount: string;
};

type UnitRecord = {
  id: number;
  property_id: number;
  property_name?: string;
  branch_name?: string;
  code: string;
  block_name?: string | null;
  floor_name?: string | null;
  unit_type: string;
  characteristics?: { room_name?: string };
  bedrooms: number;
  status: string;
  rent?: string | null;
};

type TenancyRecord = {
  unit_id: number;
  tenant_name: string;
  status: string;
};

type LandlordRecord = {
  id: number;
  branch: { id: number; name: string; town: string };
  name: string;
};

type InventoryResponse = {
  property?: PropertyRecord;
  properties?: PropertyRecord[];
  units?: UnitRecord[];
  landlords?: LandlordRecord[];
  tenancies?: TenancyRecord[];
  error?: string;
  detail?: string;
};

const fieldClass = 'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900';
const newUnitType = (): UnitTypeDraft => ({
  name: '',
  unitCount: '0',
  monthlyRent: '',
  securityDeposit: '0',
  utilities: [],
});
const newUtility = (): UnitTypeUtilityDraft => ({
  utilityType: 'water',
  name: '',
  billingMethod: 'metered',
  fixedAmount: '',
  ratePerUnit: '',
  depositAmount: '',
});
function propertyRuleStatements(value: unknown): string[] {
  if (typeof value !== 'object' || value === null || Array.isArray(value) || !('statements' in value)) {
    return [];
  }
  return Array.isArray(value.statements)
    ? value.statements.filter((statement): statement is string => typeof statement === 'string')
    : [];
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    cache: 'no-store',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  const payload = await response.json().catch(() => null) as InventoryResponse | null;
  if (!response.ok) {
    throw new Error(payload?.error || payload?.detail || `Request failed with status ${response.status}.`);
  }
  if (!payload) throw new Error('The server returned an empty response.');
  return payload as T;
}

function MoveInSettingsEditor({
  property,
  onSaved,
}: {
  property: PropertyRecord;
  onSaved: (property: PropertyRecord) => void;
}) {
  const [lettingFee, setLettingFee] = useState(property.move_in_letting_fee || '0.00');
  const [items, setItems] = useState(
    property.default_handover_items?.map((item) => ({
      name: item.name,
      quantity: String(item.quantity),
    })) ?? [],
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const result = await requestJson<InventoryResponse>(
        `/api/property-officer/properties/${property.id}/move-in-settings`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            move_in_letting_fee: lettingFee,
            default_handover_items: items.map((item) => ({
              name: item.name.trim(),
              quantity: Number(item.quantity),
            })),
          }),
        },
      );
      if (!result.property) throw new Error('The saved property settings were not returned.');
      onSaved(result.property);
      setNotice('Move-in settings saved.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to save move-in settings.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="mt-4 space-y-3 border-t border-slate-100 pt-4">
      <h3 className="text-xs font-bold uppercase tracking-wide text-slate-600">Move-in settings</h3>
      <label className="block text-xs font-semibold text-slate-700">One-time letting fee (KES)
        <input type="number" min="0" step="0.01" value={lettingFee}
          onChange={(event) => setLettingFee(event.target.value)} className={fieldClass} />
      </label>
      <p className="text-xs text-slate-500">This setting is not charged on the move-in invoice.</p>
      <div className="space-y-2">
        {items.map((item, index) => (
          <div key={index} className="grid grid-cols-[minmax(0,1fr)_7rem_auto] gap-2">
            <label className="sr-only" htmlFor={`handover-name-${property.id}-${index}`}>Item name</label>
            <input id={`handover-name-${property.id}-${index}`} maxLength={100}
              value={item.name} onChange={(event) => setItems((current) => current.map((entry, entryIndex) => (
                entryIndex === index ? { ...entry, name: event.target.value } : entry
              )))} placeholder="Keys, access cards…" className={fieldClass} />
            <label className="sr-only" htmlFor={`handover-quantity-${property.id}-${index}`}>Quantity</label>
            <input id={`handover-quantity-${property.id}-${index}`} type="number" min="0" max="32767" step="1"
              value={item.quantity} onChange={(event) => setItems((current) => current.map((entry, entryIndex) => (
                entryIndex === index ? { ...entry, quantity: event.target.value } : entry
              )))} className={fieldClass} />
            <button type="button" onClick={() => setItems((current) => current.filter((_, entryIndex) => entryIndex !== index))}
              className="px-2 text-xs font-semibold text-red-700">Remove</button>
          </div>
        ))}
        <button type="button" disabled={items.length >= 30}
          onClick={() => setItems((current) => [...current, { name: '', quantity: '0' }])}
          className="text-xs font-bold text-emerald-900 disabled:opacity-50">+ Add handover item</button>
      </div>
      {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
      {notice && <p role="status" className="text-xs text-emerald-800">{notice}</p>}
      <button type="submit" disabled={saving}
        className="rounded-lg bg-emerald-800 px-3 py-2 text-xs font-bold text-white disabled:opacity-50">
        {saving ? 'Saving…' : 'Save move-in settings'}
      </button>
    </form>
  );
}

export default function PropertyUnitManager({ mode }: { mode: 'properties' | 'units' }) {
  const { agency, branchIds } = usePropertyOfficerPortal();
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [units, setUnits] = useState<UnitRecord[]>([]);
  const [landlords, setLandlords] = useState<LandlordRecord[]>([]);
  const [tenancies, setTenancies] = useState<TenancyRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [name, setName] = useState('');
  const [propertyType, setPropertyType] = useState('Residential');
  const [location, setLocation] = useState('');
  const [address, setAddress] = useState('');
  const [branchId, setBranchId] = useState<number | ''>('');
  const [propertyBranchId, setPropertyBranchId] = useState<number | ''>('');
  const [propertySearch, setPropertySearch] = useState('');
  const [propertyTypeFilter, setPropertyTypeFilter] = useState('all');
  const [landlordFilter, setLandlordFilter] = useState<'all' | 'assigned' | 'unassigned'>('all');
  const [selectedPropertyId, setSelectedPropertyId] = useState<number | 'all'>('all');
  const [unitStatus, setUnitStatus] = useState('All statuses');
  const [unitBranchId, setUnitBranchId] = useState('all');
  const [unitTypeFilter, setUnitTypeFilter] = useState('all');
  const [bedroomFilter, setBedroomFilter] = useState('all');
  const [unitSearch, setUnitSearch] = useState('');
  const [landlordId, setLandlordId] = useState<number | ''>('');
  const [rentDueDay, setRentDueDay] = useState(10);
  const [amenities, setAmenities] = useState('');
  const [houseRules, setHouseRules] = useState(['']);
  const [handoverItems, setHandoverItems] = useState<{ name: string; quantity: string }[]>([]);
  const [unitTypes, setUnitTypes] = useState<UnitTypeDraft[]>([newUnitType()]);
  const [unitCount, setUnitCount] = useState(0);
  const unitTypeCount = unitTypes.reduce(
    (total, unitType) => total + Number(unitType.unitCount || 0),
    0,
  );
  const unitCountsMatch = unitTypeCount === unitCount;

  const visibleBranches = useMemo(
    () => (agency?.branches ?? []).filter((branch) => branchIds.has(branch.id)),
    [agency, branchIds],
  );
  const visibleProperties = useMemo(
    () => properties.filter((property) => branchIds.has(property.branch_id)),
    [branchIds, properties],
  );
  const visibleUnits = useMemo(() => {
    const propertyIds = new Set(visibleProperties.map((property) => property.id));
    return units.filter((unit) => propertyIds.has(unit.property_id));
  }, [units, visibleProperties]);
  const propertyById = useMemo(
    () => new Map(visibleProperties.map((property) => [property.id, property])),
    [visibleProperties],
  );
  const branchById = useMemo(
    () => new Map(visibleBranches.map((branch) => [branch.id, branch])),
    [visibleBranches],
  );
  const selectedProperty = visibleProperties.find((property) => property.id === selectedPropertyId) ?? null;
  const propertyTypeOptions = useMemo(
    () => [...new Set(visibleProperties.map((property) => property.property_type).filter(Boolean))].sort(),
    [visibleProperties],
  );
  const filteredProperties = useMemo(() => {
    const query = propertySearch.trim().toLowerCase();
    return visibleProperties.filter((property) => {
      const matchesBranch = propertyBranchId === '' || property.branch_id === propertyBranchId;
      const matchesType = propertyTypeFilter === 'all' || property.property_type === propertyTypeFilter;
      const matchesLandlord = landlordFilter === 'all'
        || (landlordFilter === 'assigned' ? property.landlord_id !== null : property.landlord_id === null);
      const matchesSearch = !query || [
        property.name,
        property.location,
        property.address ?? '',
        branchById.get(property.branch_id)?.name ?? '',
      ].some((value) => value.toLowerCase().includes(query));
      return matchesBranch && matchesType && matchesLandlord && matchesSearch;
    });
  }, [branchById, landlordFilter, propertyBranchId, propertySearch, propertyTypeFilter, visibleProperties]);
  const propertyPagination = usePortalPagination(filteredProperties);
  const activeTenantsByUnit = useMemo(
    () => new Map(
      tenancies.filter((tenancy) => tenancy.status === 'active').map((tenancy) => [tenancy.unit_id, tenancy.tenant_name]),
    ),
    [tenancies],
  );
  const unitTypeOptions = useMemo(
    () => [...new Set(visibleUnits
      .filter((unit) => (selectedPropertyId === 'all' || unit.property_id === selectedPropertyId)
        && (unitBranchId === 'all' || String(propertyById.get(unit.property_id)?.branch_id ?? '') === unitBranchId))
      .map((unit) => unit.unit_type)
      .filter(Boolean))].sort(),
    [propertyById, selectedPropertyId, unitBranchId, visibleUnits],
  );
  const bedroomOptions = useMemo(
    () => [...new Set(visibleUnits
      .filter((unit) => (selectedPropertyId === 'all' || unit.property_id === selectedPropertyId)
        && (unitBranchId === 'all' || String(propertyById.get(unit.property_id)?.branch_id ?? '') === unitBranchId))
      .map((unit) => unit.bedrooms))].sort((a, b) => a - b),
    [propertyById, selectedPropertyId, unitBranchId, visibleUnits],
  );
  const filteredUnits = useMemo(() => {
    const query = unitSearch.trim().toLowerCase();
    return visibleUnits.filter((unit) => {
      const property = propertyById.get(unit.property_id);
      const matchesProperty = selectedPropertyId === 'all' || unit.property_id === selectedPropertyId;
      const matchesBranch = unitBranchId === 'all' || String(property?.branch_id ?? '') === unitBranchId;
      const matchesStatus = unitStatus === 'All statuses' || unit.status.toLowerCase() === unitStatus.toLowerCase();
      const matchesType = unitTypeFilter === 'all' || unit.unit_type === unitTypeFilter;
      const matchesBedrooms = bedroomFilter === 'all' || String(unit.bedrooms) === bedroomFilter;
      const matchesSearch = !query || [
        unit.code,
        unit.characteristics?.room_name ?? '',
        unit.block_name ?? '',
        unit.floor_name ?? '',
        activeTenantsByUnit.get(unit.id) ?? '',
        unit.property_name ?? property?.name ?? '',
        unit.branch_name ?? '',
      ].some((value) => value.toLowerCase().includes(query));
      return matchesProperty && matchesBranch && matchesStatus && matchesType && matchesBedrooms && matchesSearch;
    });
  }, [activeTenantsByUnit, bedroomFilter, propertyById, selectedPropertyId, unitBranchId, unitSearch, unitStatus, unitTypeFilter, visibleUnits]);
  const unitPagination = usePortalPagination(filteredUnits);
  const selectedBranchLandlords = landlords.filter((landlord) => landlord.branch.id === branchId);

  const load = useCallback(async () => {
    if (!agency) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const [propertyResponse, unitResponse, landlordResponse, tenancyResponse] = await Promise.all([
        requestJson<InventoryResponse>('/api/property-officer/properties'),
        requestJson<InventoryResponse>('/api/property-officer/units'),
        requestJson<InventoryResponse>(`/api/property-officer/landlords?agency_id=${agency.id}`),
        requestJson<InventoryResponse>('/api/property-officer/tenancies'),
      ]);
      setProperties(propertyResponse.properties ?? []);
      setUnits(unitResponse.units ?? []);
      setLandlords(landlordResponse.landlords ?? []);
      setTenancies(tenancyResponse.tenancies ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load property inventory.');
    } finally {
      setLoading(false);
    }
  }, [agency]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  useEffect(() => {
    if (branchId === '' || !branchIds.has(branchId)) {
      setBranchId(visibleBranches[0]?.id ?? '');
      setLandlordId('');
    }
  }, [branchId, branchIds, visibleBranches]);

  useEffect(() => {
    if (selectedPropertyId !== 'all' && !visibleProperties.some((property) => property.id === selectedPropertyId)) {
      setSelectedPropertyId('all');
    }
  }, [selectedPropertyId, visibleProperties]);

  async function createProperty(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!unitCountsMatch) {
      setError(
        `Unit type counts total ${unitTypeCount}, but the property total is ${unitCount}.`,
      );
      return;
    }
    if (handoverItems.some(
      (item) => !item.name.trim()
        || !/^\d+$/.test(item.quantity)
        || Number(item.quantity) > 32767,
    )) {
      setError('Enter a name and a whole-number quantity from 0 to 32,767 for each handover item.');
      return;
    }
    setSubmitting(true);
    setError('');
    setNotice('');
    try {
      const result = await requestJson<{ property: PropertyRecord; units: UnitRecord[]; error?: string }>(
        '/api/property-officer/properties',
        {
          method: 'POST',
          body: JSON.stringify({
            branch_id: branchId,
            landlord_id: landlordId || null,
            name: name.trim(),
            property_type: propertyType.trim(),
            location: location.trim(),
            address: address.trim(),
            rent_due_day: rentDueDay,
            expected_unit_count: unitCount,
            unit_types: unitTypes.map((unitType) => ({
              name: unitType.name.trim(),
              unit_count: Number(unitType.unitCount),
              monthly_rent: unitType.monthlyRent,
              security_deposit: unitType.securityDeposit || '0',
              utilities: unitType.utilities.map((utility) => ({
                utility_type: utility.utilityType,
                name: utility.name.trim(),
                billing_method: utility.billingMethod,
                fixed_amount: utility.billingMethod === 'fixed' ? utility.fixedAmount : null,
                rate_per_unit: utility.billingMethod === 'metered' ? utility.ratePerUnit : null,
                deposit_amount: utility.depositAmount || null,
              })),
            })),
            amenities: amenities.split(',').map((item) => item.trim()).filter(Boolean),
            house_rules: {
              statements: houseRules.map((rule) => rule.trim()).filter(Boolean),
            },
            default_handover_items: handoverItems.map((item) => ({
              name: item.name.trim(),
              quantity: Number(item.quantity),
            })),
          }),
        },
      );
      setNotice(`Registered ${result.property.name}. Import its unit records from Units & vacancies when ready.`);
      setName('');
      setLocation('');
      setAddress('');
      setLandlordId('');
      setHouseRules(['']);
      setHandoverItems([]);
      setUnitTypes([newUnitType()]);
      setUnitCount(0);
      setReloadKey((key) => key + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create the property.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex justify-end">
        <button type="button" onClick={() => setReloadKey((key) => key + 1)} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700">
          <RefreshCw size={15} /> Refresh inventory
        </button>
      </header>

      {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">{notice}</p>}
      {loading && <PortalLoading label="Loading your property and unit records…" rows={5} />}
      {!loading && visibleBranches.length === 0 && <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">No assigned branches are available for this agency.</p>}

      {!loading && visibleBranches.length > 0 && mode === 'properties' && (
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(28rem,1fr)]">
          <section className="space-y-4">
            <section aria-label="Filter properties" className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:grid-cols-2 xl:grid-cols-2">
              <label className="text-xs font-semibold text-slate-600">Search properties
                <input value={propertySearch} onChange={(event) => setPropertySearch(event.target.value)} placeholder="Name, location, or address" className={fieldClass} />
              </label>
              <label className="text-xs font-semibold text-slate-600">Branch
                <select className={fieldClass} value={propertyBranchId} onChange={(event) => setPropertyBranchId(event.target.value ? Number(event.target.value) : '')}>
                  <option value="">All assigned branches</option>
                  {visibleBranches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
                </select>
              </label>
              <label className="text-xs font-semibold text-slate-600">Property type
                <select className={fieldClass} value={propertyTypeFilter} onChange={(event) => setPropertyTypeFilter(event.target.value)}>
                  <option value="all">All property types</option>
                  {propertyTypeOptions.map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </label>
              <label className="text-xs font-semibold text-slate-600">Landlord assignment
                <select className={fieldClass} value={landlordFilter} onChange={(event) => setLandlordFilter(event.target.value as typeof landlordFilter)}>
                  <option value="all">All assignments</option>
                  <option value="assigned">Landlord assigned</option>
                  <option value="unassigned">No landlord assigned</option>
                </select>
              </label>
              <p className="self-end pb-2 text-xs text-slate-500 sm:col-span-2">
                {filteredProperties.length} matching propert{filteredProperties.length === 1 ? 'y' : 'ies'}
              </p>
            </section>
            {mode === 'properties' ? (
              propertyPagination.pageRecords.map((property) => (
                <article key={property.id} data-portal-card className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-7">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-lg font-bold tracking-tight text-slate-900">{property.name}</h2>
                        <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-emerald-900">{property.property_type}</span>
                      </div>
                      <p className="mt-2 text-sm text-slate-600">{property.location}</p>
                      {property.address && <p className="mt-1 text-xs text-slate-500">{property.address}</p>}
                    </div>
                    <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[#edf4e9] text-emerald-800">
                      <Building2 size={22} />
                    </span>
                  </div>
                  <div className="mt-5 grid gap-2 border-y border-slate-100 py-4 text-xs sm:grid-cols-2">
                    <span className="rounded-xl bg-slate-50 px-3 py-2.5"><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-400">Branch</span><span className="mt-1 block font-semibold text-slate-700">{branchById.get(property.branch_id)?.name || 'Branch'}</span></span>
                    <span className="rounded-xl bg-slate-50 px-3 py-2.5"><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-400">Landlord</span><span className="mt-1 block font-semibold text-slate-700">{property.landlord_id ? landlords.find((item) => item.id === property.landlord_id)?.name || 'Assigned' : 'Not assigned'}</span></span>
                    <span className="rounded-xl bg-slate-50 px-3 py-2.5"><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-400">Rent due</span><span className="mt-1 block font-semibold text-slate-700">Day {property.rent_due_day} of each month</span></span>
                    <span className="rounded-xl bg-slate-50 px-3 py-2.5"><span className="block text-[10px] font-bold uppercase tracking-wide text-slate-400">Unit inventory</span><span className="mt-1 block font-semibold text-slate-700">{units.filter((unit) => unit.property_id === property.id).length} recorded · {property.expected_unit_count} expected</span></span>
                  </div>
                  {property.unit_types?.length ? (
                    <div className="mt-4 space-y-2 border-t border-slate-100 pt-3">
                      <p className="text-xs font-bold uppercase tracking-wide text-slate-500">Saved unit types</p>
                      {property.unit_types.map((unitType) => (
                        <div key={unitType.id} className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                          <p className="font-semibold text-slate-800">
                            {unitType.name} · {unitType.unit_count} units · KES {Number(unitType.monthly_rent).toLocaleString()} rent · KES {Number(unitType.security_deposit).toLocaleString()} security deposit
                          </p>
                          {unitType.utilities.length > 0 && (
                            <p className="mt-1">
                              Utilities: {unitType.utilities.map((utility) => (
                                `${utility.name} (${utility.billing_method === 'fixed' ? `KES ${utility.fixed_amount}/month` : `KES ${utility.rate_per_unit}/unit`}${utility.deposit_amount ? `, KES ${utility.deposit_amount} deposit` : ''})`
                              )).join('; ')}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : property.default_unit_type ? (
                    <p className="mt-3 text-xs text-slate-500">
                      Legacy default: {property.default_unit_type} · KES {Number(property.default_rent).toLocaleString()} monthly
                    </p>
                  ) : null}
                  {Array.isArray(property.amenities) && property.amenities.length > 0 && <p className="mt-3 text-xs text-slate-500">Amenities: {property.amenities.map(String).join(', ')}</p>}
                  {propertyRuleStatements(property.house_rules).length > 0 && (
                    <div className="mt-3 text-xs text-slate-500">
                      <p className="font-semibold">House rules</p>
                      <ul className="mt-1 list-disc space-y-1 pl-5">
                        {propertyRuleStatements(property.house_rules).map((rule, index) => <li key={`${index}-${rule}`}>{rule}</li>)}
                      </ul>
                    </div>
                  )}
                  <MoveInSettingsEditor
                    property={property}
                    onSaved={(updatedProperty) => setProperties((current) => current.map((item) => (
                      item.id === updatedProperty.id ? updatedProperty : item
                    )))}
                  />
                </article>
              ))
            ) : null}
            {filteredProperties.length === 0 && <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">No properties match the selected filters.</p>}
            <PortalPagination {...propertyPagination} total={filteredProperties.length} onPageChange={propertyPagination.setPage} onPageSizeChange={propertyPagination.setPageSize} />
          </section>

          <form onSubmit={createProperty} data-portal-card className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-7">
            <div>
            <h2 className="font-bold text-slate-900">Register property</h2>
            <p className="mt-1 text-xs text-slate-500">This saves the property profile only. Existing units are imported separately from Units & vacancies.</p>
            </div>
            <label className="block text-sm font-semibold">Branch
              <select required className={fieldClass} value={branchId} onChange={(event) => {
                setBranchId(Number(event.target.value));
                setLandlordId('');
              }}>
                {visibleBranches.map((branch: Branch) => <option key={branch.id} value={branch.id}>{branch.name} · {branch.town}</option>)}
              </select>
            </label>
            <label className="block text-sm font-semibold">Property name
              <input required maxLength={255} className={fieldClass} value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm font-semibold">Property type
                <input required maxLength={100} className={fieldClass} value={propertyType} onChange={(event) => setPropertyType(event.target.value)} />
              </label>
              <label className="block text-sm font-semibold">Location
                <input required maxLength={255} className={fieldClass} value={location} onChange={(event) => setLocation(event.target.value)} />
              </label>
            </div>
            <label className="block text-sm font-semibold">Street address (optional)
              <input maxLength={255} className={fieldClass} value={address} onChange={(event) => setAddress(event.target.value)} />
            </label>
            <label className="block text-sm font-semibold">Landlord (optional)
              <select className={fieldClass} value={landlordId} onChange={(event) => setLandlordId(event.target.value ? Number(event.target.value) : '')}>
                <option value="">Not assigned</option>
                {selectedBranchLandlords.map((landlord) => <option key={landlord.id} value={landlord.id}>{landlord.name}</option>)}
              </select>
            </label>
            <label className="block text-sm font-semibold">Amenities (comma separated)
              <input className={fieldClass} value={amenities} onChange={(event) => setAmenities(event.target.value)} placeholder="Parking, water tank, security" />
            </label>
            <section className="space-y-3 rounded-xl border border-slate-200 p-4" aria-label="Move-in handover items">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-bold text-slate-900">Move-in handover items (optional)</h3>
                  <p className="mt-1 text-xs text-slate-500">
                    Add each item tenants should receive and the expected quantity. Leave this empty if there are no items to issue.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={handoverItems.length >= 30}
                  onClick={() => setHandoverItems((current) => [...current, { name: '', quantity: '0' }])}
                  className="shrink-0 rounded-lg border border-emerald-800 px-3 py-2 text-xs font-bold text-emerald-900 disabled:opacity-50"
                >
                  Add item
                </button>
              </div>
              {handoverItems.length === 0 ? (
                <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                  No handover items configured.
                </p>
              ) : (
                <div className="space-y-2">
                  {handoverItems.map((item, index) => (
                    <div key={index} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem_auto]">
                      <label className="block text-xs font-semibold text-slate-700">
                        Item name
                        <input
                          required
                          maxLength={100}
                          className={fieldClass}
                          value={item.name}
                          onChange={(event) => setHandoverItems((current) => current.map((entry, entryIndex) => (
                            entryIndex === index ? { ...entry, name: event.target.value } : entry
                          )))}
                          placeholder="Keys, access cards…"
                        />
                      </label>
                      <label className="block text-xs font-semibold text-slate-700">
                        Quantity
                        <input
                          required
                          min="0"
                          max="32767"
                          step="1"
                          type="number"
                          className={fieldClass}
                          value={item.quantity}
                          onChange={(event) => setHandoverItems((current) => current.map((entry, entryIndex) => (
                            entryIndex === index ? { ...entry, quantity: event.target.value } : entry
                          )))}
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() => setHandoverItems((current) => current.filter((_, entryIndex) => entryIndex !== index))}
                        className="self-end px-2 py-2 text-xs font-semibold text-red-700"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section className="space-y-2 rounded-xl border border-slate-200 p-4">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-bold text-slate-900">House rules</h3>
                <button type="button" onClick={() => setHouseRules((current) => [...current, ''])} className="text-xs font-bold text-emerald-900">+ Add rule</button>
              </div>
              {houseRules.map((rule, index) => (
                <div key={index} className="flex items-start gap-2">
                  <textarea maxLength={500} rows={2} className={fieldClass} value={rule} onChange={(event) => setHouseRules((current) => current.map((item, ruleIndex) => ruleIndex === index ? event.target.value : item))} placeholder={`Rule ${index + 1}`} />
                  {houseRules.length > 1 && <button type="button" onClick={() => setHouseRules((current) => current.filter((_, ruleIndex) => ruleIndex !== index))} className="pt-2 text-xs font-semibold text-red-700">Remove</button>}
                </div>
              ))}
            </section>
            <label className="block text-sm font-semibold">Total unit count
              <input required min="0" max="500" step="1" type="number" className={fieldClass} value={unitCount} onChange={(event) => setUnitCount(Number(event.target.value))} />
            </label>
            <p className={`text-xs ${unitCountsMatch ? 'text-slate-500' : 'text-red-700'}`} aria-live="polite">
              Unit type counts: {unitTypeCount} of {unitCount} total units.
            </p>
            <section className="space-y-3 rounded-xl border border-slate-200 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-bold text-slate-900">Unit types and charges</h3>
                  <p className="mt-1 text-xs text-slate-500">Enter how many units of each type the property contains. Their counts must add up to the total unit count.</p>
                </div>
                <button type="button" onClick={() => setUnitTypes((current) => [...current, newUnitType()])} className="shrink-0 rounded-lg border border-emerald-800 px-3 py-2 text-xs font-bold text-emerald-900">
                  Add unit type
                </button>
              </div>
              {unitTypes.map((unitType, typeIndex) => (
                <fieldset key={typeIndex} className="space-y-3 rounded-lg bg-slate-50 p-3">
                  <legend className="px-1 text-xs font-bold text-slate-700">Unit type {typeIndex + 1}</legend>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <label className="block text-sm font-semibold">Type name
                      <input required maxLength={100} className={fieldClass} value={unitType.name} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, name: event.target.value } : item))} placeholder="Bedsitter, One bedroom…" />
                    </label>
                    <label className="block text-sm font-semibold">Units of this type
                      <input required min="0" max="500" step="1" type="number" className={fieldClass} value={unitType.unitCount} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, unitCount: event.target.value } : item))} />
                    </label>
                    <label className="block text-sm font-semibold">Monthly rent (KES)
                      <input required min="0" step="0.01" type="number" className={fieldClass} value={unitType.monthlyRent} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, monthlyRent: event.target.value } : item))} />
                    </label>
                    <label className="block text-sm font-semibold">Security deposit (KES)
                      <input required min="0" step="0.01" type="number" className={fieldClass} value={unitType.securityDeposit} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, securityDeposit: event.target.value } : item))} />
                    </label>
                  </div>
                  <div className="space-y-2">
                    {unitType.utilities.map((utility, utilityIndex) => (
                      <div key={utilityIndex} className="grid gap-2 rounded-lg border border-slate-200 bg-white p-3 sm:grid-cols-2">
                        <label className="block text-xs font-semibold">Utility name
                          <input required maxLength={100} className={fieldClass} value={utility.name} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: item.utilities.map((entry, entryIndex) => entryIndex === utilityIndex ? { ...entry, name: event.target.value } : entry) } : item))} placeholder="Water, garbage…" />
                        </label>
                        <label className="block text-xs font-semibold">Utility type
                          <select className={fieldClass} value={utility.utilityType} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: item.utilities.map((entry, entryIndex) => entryIndex === utilityIndex ? { ...entry, utilityType: event.target.value } : entry) } : item))}>
                            <option value="water">Water</option>
                            <option value="electricity">Electricity</option>
                            <option value="other">Other</option>
                          </select>
                        </label>
                        <label className="block text-xs font-semibold">Billing method
                          <select className={fieldClass} value={utility.billingMethod} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: item.utilities.map((entry, entryIndex) => entryIndex === utilityIndex ? { ...entry, billingMethod: event.target.value } : entry) } : item))}>
                            <option value="fixed">Fixed monthly amount</option>
                            <option value="metered">Metered rate</option>
                          </select>
                        </label>
                        {utility.billingMethod === 'fixed' ? (
                          <label className="block text-xs font-semibold">Monthly amount (KES)
                            <input required min="0" step="0.01" type="number" className={fieldClass} value={utility.fixedAmount} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: item.utilities.map((entry, entryIndex) => entryIndex === utilityIndex ? { ...entry, fixedAmount: event.target.value } : entry) } : item))} />
                          </label>
                        ) : (
                          <label className="block text-xs font-semibold">Rate per unit (KES)
                            <input required min="0" step="0.0001" type="number" className={fieldClass} value={utility.ratePerUnit} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: item.utilities.map((entry, entryIndex) => entryIndex === utilityIndex ? { ...entry, ratePerUnit: event.target.value } : entry) } : item))} />
                          </label>
                        )}
                        <label className="block text-xs font-semibold">Utility deposit (KES, optional)
                          <input min="0" step="0.01" type="number" className={fieldClass} value={utility.depositAmount} onChange={(event) => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: item.utilities.map((entry, entryIndex) => entryIndex === utilityIndex ? { ...entry, depositAmount: event.target.value } : entry) } : item))} />
                        </label>
                        <button type="button" onClick={() => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: item.utilities.filter((_, entryIndex) => entryIndex !== utilityIndex) } : item))} className="justify-self-start text-xs font-semibold text-red-700">Remove utility</button>
                      </div>
                    ))}
                    <button type="button" onClick={() => setUnitTypes((current) => current.map((item, index) => index === typeIndex ? { ...item, utilities: [...item.utilities, newUtility()] } : item))} className="text-xs font-bold text-emerald-900">+ Add utility</button>
                  </div>
                  {unitTypes.length > 1 && <button type="button" onClick={() => setUnitTypes((current) => current.filter((_, index) => index !== typeIndex))} className="text-xs font-semibold text-red-700">Remove unit type</button>}
                </fieldset>
              ))}
            </section>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm font-semibold">Rent due day
                <input required min="1" max="28" step="1" type="number" className={fieldClass} value={rentDueDay} onChange={(event) => setRentDueDay(Number(event.target.value))} />
              </label>
            </div>
            <button disabled={submitting || visibleBranches.length === 0 || !unitCountsMatch} className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-800 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-50">
              <Plus size={16} /> {submitting ? 'Saving property…' : 'Register property'}
            </button>
          </form>
        </div>
      )}
      {!loading && visibleBranches.length > 0 && mode === 'units' && (
        <section className="space-y-4">
          <div data-portal-card className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-5 sm:grid-cols-2 xl:grid-cols-4">
            <label className="text-sm font-semibold text-slate-700">Branch
              <select className={fieldClass} value={unitBranchId} onChange={(event) => {
                setUnitBranchId(event.target.value);
                setSelectedPropertyId('all');
                setUnitTypeFilter('all');
                setBedroomFilter('all');
              }}>
                <option value="all">All assigned branches</option>
                {visibleBranches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
              </select>
            </label>
            <label className="min-w-64 flex-1 text-sm font-semibold text-slate-700">Property
              <select className={fieldClass} value={selectedPropertyId} onChange={(event) => {
                setSelectedPropertyId(event.target.value === 'all' ? 'all' : Number(event.target.value));
                setUnitTypeFilter('all');
                setBedroomFilter('all');
              }}>
                <option value="all">All properties</option>
                {visibleProperties.filter((property) => unitBranchId === 'all' || String(property.branch_id) === unitBranchId).map((property) => <option key={property.id} value={property.id}>{property.name} · {branchById.get(property.branch_id)?.name || 'Branch'}</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-slate-700">Status
              <select className={fieldClass} value={unitStatus} onChange={(event) => setUnitStatus(event.target.value)}>
                {['All statuses', 'Draft', 'Vacant', 'Booked', 'Occupied', 'Notice Given', 'Deposit Pending', 'Maintenance', 'Archived'].map((status) => <option key={status}>{status}</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-slate-700">Unit type
              <select className={fieldClass} value={unitTypeFilter} onChange={(event) => setUnitTypeFilter(event.target.value)}>
                <option value="all">All unit types</option>
                {unitTypeOptions.map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-slate-700">Bedrooms
              <select className={fieldClass} value={bedroomFilter} onChange={(event) => setBedroomFilter(event.target.value)}>
                <option value="all">Any bedroom count</option>
                {bedroomOptions.map((bedrooms) => <option key={bedrooms} value={bedrooms}>{bedrooms} bedroom{bedrooms === 1 ? '' : 's'}</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-slate-700 sm:col-span-2">Search units
              <input value={unitSearch} onChange={(event) => setUnitSearch(event.target.value)} placeholder="Unit code, tenant, block, floor, or property" className={fieldClass} />
            </label>
            <div className="flex items-end justify-between gap-3 sm:col-span-2">
              <p className="pb-2 text-xs text-slate-500">{filteredUnits.length} matching unit{filteredUnits.length === 1 ? '' : 's'}</p>
            {selectedProperty && <Link href={`/property-officer/imports?property_id=${selectedProperty.id}`} className="rounded-lg bg-emerald-800 px-4 py-2.5 text-sm font-bold text-white">Import units for this property</Link>}
            </div>
          </div>
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                  <tr>{['Unit code', 'Tenant', 'Property', 'Branch', 'Type', 'Bedrooms', 'Rent / month', 'Status'].map((label) => <th key={label} className="px-4 py-3">{label}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {unitPagination.pageRecords.map((unit) => {
                    const property = propertyById.get(unit.property_id);
                    return (
                      <tr key={unit.id}>
                        <td className="px-4 py-3">
                          <span className="font-mono font-semibold">{unit.code}</span>
                          {unit.characteristics?.room_name && <span className="ml-2 text-xs text-slate-500">{unit.characteristics.room_name}</span>}
                          {(unit.block_name || unit.floor_name) && <span className="mt-1 block text-xs text-slate-500">{[unit.block_name, unit.floor_name].filter(Boolean).join(' · ')}</span>}
                        </td>
                        <td className="px-4 py-3">{activeTenantsByUnit.get(unit.id) || '—'}</td>
                        <td className="px-4 py-3">{unit.property_name || property?.name || 'Property'}</td>
                        <td className="px-4 py-3">{unit.branch_name || (property ? branchById.get(property.branch_id)?.name : null) || '—'}</td>
                        <td className="px-4 py-3">{unit.unit_type}</td>
                        <td className="px-4 py-3">{unit.bedrooms}</td>
                        <td className="px-4 py-3">{unit.rent === null || unit.rent === undefined ? 'Not set' : `KES ${Number(unit.rent).toLocaleString()}`}</td>
                        <td className="px-4 py-3">{unit.status}</td>
                      </tr>
                    );
                  })}
                  {!filteredUnits.length && <tr><td className="px-4 py-6 text-center text-slate-500" colSpan={8}>{visibleUnits.length ? 'No units match the selected filters.' : 'Register a property or import its unit records to populate this view.'}</td></tr>}
                </tbody>
              </table>
            </div>
            <PortalPagination {...unitPagination} total={filteredUnits.length} onPageChange={unitPagination.setPage} onPageSizeChange={unitPagination.setPageSize} />
          </section>
        </section>
      )}
    </main>
  );
}
