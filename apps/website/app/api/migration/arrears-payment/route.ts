export async function POST() {
  return Response.json(
    { error: 'Legacy arrears edits are disabled. Import opening balances and payments through the audited Property Officer import workflow.' },
    { status: 410 },
  );
}
