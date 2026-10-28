export function generateTenantActivationCode(unitCode: string): string {
  const suffix = Math.random().toString(36).slice(2, 8).toUpperCase();
  return `${unitCode.toUpperCase()}-${suffix}`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]!);
}

export async function createTenantActivation(
  supabaseAdmin: any,
  input: {
    unitId: string;
    unitCode: string;
    tenantId: string;
    fullName: string;
    email: string;
    origin: string;
    propertyName?: string;
    createClaimCode?: boolean;
  }
) {
  const expiresAt = new Date(Date.now() + 48 * 60 * 60 * 1000).toISOString();
  let claimCode: { id: string; code: string; expires_at: string } | null = null;
  if (input.createClaimCode !== false) {
    const code = generateTenantActivationCode(input.unitCode);
    const { data, error } = await supabaseAdmin
      .from('claim_codes')
      .insert({ unit_id: input.unitId, code, expires_at: expiresAt })
      .select('id, code, expires_at')
      .single();

    if (error || !data) {
      throw new Error(`Failed to create tenant activation code: ${error?.message || 'unknown error'}`);
    }
    claimCode = data;
  }

  const redirectTo = `${input.origin}/activate?mode=email${claimCode ? `&claimId=${claimCode.id}` : ''}`;
  const { data: linkData, error: linkError } = await supabaseAdmin.auth.admin.generateLink({
    type: 'recovery',
    email: input.email,
    options: { redirectTo },
  });

  let emailError: Error | null = linkError
    ? new Error(linkError.message)
    : linkData?.properties?.action_link
      ? null
      : new Error('The authentication provider did not return an activation link.');
  const brevoApiKey = process.env.BREVO_API_KEY;
  const senderEmail = process.env.BREVO_SENDER_EMAIL;

  // Fetch landlord's property name
  const senderName = input.propertyName || process.env.BREVO_SENDER_NAME || 'Property Management';
  const safeSenderName = escapeHtml(senderName);
  const safeFullName = escapeHtml(input.fullName);
  const safeEmail = escapeHtml(input.email);
  const safeUnitCode = escapeHtml(input.unitCode);

  if (!emailError && brevoApiKey && senderEmail && linkData?.properties?.action_link) {
    try {
      const response = await fetch('https://api.brevo.com/v3/smtp/email', {
        method: 'POST',
        headers: {
          accept: 'application/json',
          'api-key': brevoApiKey,
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          sender: { email: senderEmail, name: senderName },
          to: [{ email: input.email, name: input.fullName }],
          subject: `Activate your ${senderName} account`,
          htmlContent: `
            <p>Hi ${safeFullName},</p>
            <p>Your tenant account for ${safeSenderName}${safeUnitCode ? `, unit ${safeUnitCode}` : ''} is ready to activate.</p>
            <p>Activate your account and create your password using the button below:</p>
            <p><a href="${escapeHtml(linkData.properties.action_link)}" style="background:#059669;color:#fff;padding:12px 18px;text-decoration:none;border-radius:6px;display:inline-block">Activate my account</a></p>
            <p>This activation link is for ${safeEmail} and expires according to your account security settings.</p>
            <p>Regards,<br>${safeSenderName}</p>
          `,
        }),
      });

      if (!response.ok) {
        const details = await response.text();
        emailError = new Error(`Brevo API ${response.status}: ${details}`);
      }
    } catch (error) {
      emailError = error instanceof Error ? error : new Error('Failed to send activation email.');
    }
  } else if (!emailError) {
    const { error: smtpError } = await supabaseAdmin.auth.resetPasswordForEmail(input.email, { redirectTo });
    emailError = smtpError ? new Error(smtpError.message) : null;
  }

  if (emailError) {
    console.error('[tenant-activation] Supabase email failed', {
      email: input.email,
      errorName: emailError.name,
      errorMessage: emailError.message,
      redirectTo,
      emailProvider: brevoApiKey ? 'Brevo API' : 'Supabase Dashboard SMTP configuration',
    });
  } else {
    console.info('[tenant-activation] Supabase accepted activation email', {
      email: input.email,
      redirectTo,
    });
  }

  return {
    claimCode,
    emailSent: !emailError,
    emailError: emailError?.message || null,
  };
}