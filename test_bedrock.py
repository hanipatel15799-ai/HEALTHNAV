from vertex_client import get_vertex_client, get_vertex_model_name

client = get_vertex_client()
model = get_vertex_model_name()

resp = client.models.generate_content(
    model=model,
    contents="Say hello in one short sentence."
)

print(resp.text)