export async function POST() {
  return Response.json(
    { error: 'The legacy manual migration endpoint is retired. Use Property Officer onboarding or the branch import preview.' },
    { status: 410 },
  );
}
