---
name: slow-multi-step
description: A deliberately long job (read, analyse, write two files) for testing queue, cancel and history
---
Do these steps one at a time, delegating each to the right subagent, and do not skip any:
1. Read the attached file, if there is one (otherwise use the text in `fields`).
2. Write a file `outline.md` with a detailed 10-point outline of its contents.
3. Write a second file `questions.md` with 10 follow-up questions someone might ask about it.
4. Reply with a one-paragraph recap and name both files.
