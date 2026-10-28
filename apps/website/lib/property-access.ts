import { SupabaseClient } from '@supabase/supabase-js';

export async function getActivePropertyId(admin: SupabaseClient, userId: string) {
  const { data: profile, error: profileError } = await admin
    .from('users')
    .select('active_property_id')
    .eq('id', userId)
    .maybeSingle();
  if (profileError) return { propertyId: null, error: profileError.message };
  if (!profile?.active_property_id) return { propertyId: null, error: 'Set up a property before using property management.' };

  const { data: membership, error: membershipError } = await admin
    .from('property_members')
    .select('property_id')
    .eq('property_id', profile.active_property_id)
    .eq('user_id', userId)
    .maybeSingle();
  if (membershipError) return { propertyId: null, error: membershipError.message };
  if (!membership) return { propertyId: null, error: 'You do not have access to the selected property.' };

  return { propertyId: String(membership.property_id), error: null };
}

export async function getPropertyUnitIds(admin: SupabaseClient, propertyId: string) {
  const { data, error } = await admin.from('units').select('id').eq('property_id', propertyId);
  if (error) return { unitIds: null, error: error.message };
  return { unitIds: (data || []).map((unit) => String(unit.id)), error: null };
}