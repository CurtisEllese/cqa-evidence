import { useStore } from "../store";

export function DatasetSummary() {
  const { data, dataset } = useStore();

  return (
    <div className="dataset-summary" role="status">
      <strong>
        {dataset
          ? `Последний загруженный набор: ${dataset.group}`
          : data.conversations.length
            ? "Группа набора не указана"
            : "Набор не загружен"}
      </strong>
      {dataset && <span>Записей в наборе: {dataset.total}</span>}
      {dataset && (
        <span>
          Обновлено: {new Date(dataset.fetchedAt).toLocaleString("ru-RU")}
        </span>
      )}
      <span>
        Всего диалогов в рабочем пространстве: {data.conversations.length}
      </span>
    </div>
  );
}
