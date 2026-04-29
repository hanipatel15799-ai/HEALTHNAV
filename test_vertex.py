from vertex_client import get_vertex_client, get_vertex_model_name

client = get_vertex_client()
model = get_vertex_model_name()
response = client.models.generate_content(model=model, contents="Reply with exactly: Vertex connection successful.")
print(response.text)
