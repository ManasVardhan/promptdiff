# Getting Started with promptdiff

A step-by-step guide to get up and running from scratch.

## Prerequisites

You need **Python 3.10 or newer** installed on your machine.

**Check if you have Python:**
```bash
python3 --version
```
If you see `Python 3.10.x` or higher, you're good. If not, download it from [python.org](https://www.python.org/downloads/).

## Step 1: Clone the repository

```bash
git clone https://github.com/ManasVardhan/promptdiff.git
cd promptdiff
```

## Step 2: Create a virtual environment

```bash
python3 -m venv venv
```

**Activate it:**

- **Mac/Linux:** `source venv/bin/activate`
- **Windows:** `venv\Scripts\activate`

## Step 3: Install the package

```bash
pip install -e ".[dev]"
```

## Step 4: Run the tests

```bash
pytest tests/ -v
```

All tests should pass.

## Step 5: Try it out

### 5a. Initialize a prompt store

```bash
mkdir my-prompts
cd my-prompts
promptdiff init
```

This creates a `.promptdiff/` directory where versions are tracked (similar to `.git/`).

### 5b. Add your first prompt

Create a prompt file:
```bash
echo "You are a helpful assistant. Answer questions clearly and concisely." > assistant.txt
```

Track it:
```bash
promptdiff add assistant.txt --tag "v1" --message "Initial version"
```

### 5c. Make a change and see the diff

Edit the prompt:
```bash
echo "You are a helpful AI assistant. Answer questions clearly, concisely, and with examples when appropriate. Always be friendly." > assistant.txt
```

Add the new version:
```bash
promptdiff add assistant.txt --tag "v2" --message "Added examples and friendliness"
```

Now see what changed:
```bash
promptdiff diff assistant.txt v1 v2
```

You'll see a colored diff showing exactly what was added, removed, and changed - plus a semantic similarity score.

### 5d. View the history

```bash
promptdiff log assistant.txt
```

Shows all versions with tags, timestamps, and messages.

### 5e. Generate a changelog

```bash
promptdiff changelog assistant.txt
```

### 5f. Run the full example workflow

```bash
cd ..
python examples/workflow.py
```

## Step 6: Use the Python API

Create a file called `test_it.py`:

```python
from promptdiff import PromptStore, diff_texts

# Create a store
store = PromptStore("./my-store")

# Add versions
store.add("greeting", "Hello, how can I help?", tag="v1")
store.add("greeting", "Hi there! How can I assist you today?", tag="v2")

# Get the diff
result = diff_texts(
    "Hello, how can I help?",
    "Hi there! How can I assist you today?"
)

print(f"Lines added: {result.additions}")
print(f"Lines removed: {result.removals}")
print(f"Similarity: {result.similarity:.2%}")
```

Run it:
```bash
python test_it.py
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `python3: command not found` | Install Python from [python.org](https://www.python.org/downloads/) |
| `No module named promptdiff` | Make sure you ran `pip install -e ".[dev]"` with the venv activated |
| `promptdiff: command not found` | Make sure your venv is activated |
| Tests fail | Make sure you're on the latest `main` branch: `git pull origin main` |

## What's next?

- Read the full [README](README.md) for semantic diff, evaluation, and CI integration
- Check `examples/` for advanced workflows
- Try integrating prompt versioning into your own LLM projects
