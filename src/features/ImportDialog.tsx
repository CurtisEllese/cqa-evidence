import { useRef, useState } from "react";
import { FileJson, Upload, ArrowRight } from "lucide-react";
import { Button, Modal, Notice } from "../components/ui";
import { parseImport } from "../domain/import";
import { useStore } from "../store";

export function ImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const { data, importConversations } = useStore();
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  async function readFile(file?: File) {
    if (!file) return;
    setError("");
    if (file.size > 5 * 1024 * 1024) {
      setError("Файл должен быть меньше 5 МБ");
      return;
    }
    setBusy(true);
    try {
      setText(await file.text());
      setFilename(file.name);
    } catch {
      setError("Не удалось прочитать файл");
    } finally {
      setBusy(false);
    }
  }
  function submit() {
    try {
      const imported = parseImport(
        text,
        data.conversations.map((item) => item.id),
      );
      if (!imported.length) {
        setError("Все диалоги уже загружены. Выберите другой набор.");
        return;
      }
      importConversations(imported);
      onImported();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Проверьте формат файла");
    }
  }
  return (
    <Modal title="Загрузить диалоги" onClose={onClose}>
      <p className="modal-intro">
        Добавьте транскрипты из набора организаторов. Исходные реплики и метки
        времени сохранятся.
      </p>
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void readFile(e.dataTransfer.files[0]);
        }}
      >
        <FileJson size={32} />
        <h3>{filename || "Перетащите JSON-файл сюда"}</h3>
        <p>Ответ API организаторов или массив записей · до 5 МБ</p>
        <Button onClick={() => fileInput.current?.click()}>
          <Upload size={16} />
          Выбрать файл
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept=".json,application/json"
          className="sr-only"
          aria-label="JSON-файл диалогов"
          onChange={(e) => void readFile(e.target.files?.[0])}
        />
      </div>
      <label className="field-label" htmlFor="json-input">
        Или вставьте JSON
      </label>
      <textarea
        id="json-input"
        className="code-input"
        placeholder={'{ "isSuccess": true, "data": { "records": [...] } }'}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setFilename("");
          setError("");
        }}
        rows={5}
      />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <Notice>
        Загрузка работает локально. Для автоматической оценки новых диалогов
        команда подключит сервис ИИ.
      </Notice>
      <div className="modal-actions">
        <Button onClick={onClose}>Отмена</Button>
        <Button
          variant="primary"
          disabled={!text.trim() || busy}
          onClick={submit}
        >
          Загрузить
          <ArrowRight size={16} />
        </Button>
      </div>
    </Modal>
  );
}
